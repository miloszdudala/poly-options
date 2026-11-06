"""Step 4 - prob: the probability that YES wins (`fair`).

| variant | fair | decided / open |
|---|---|---|
| `regular_hours` | touch probability over regular hours only (`maths.touch_probability`): each session an unwatched overnight gap carrying `1-f` of the day's variance, then a watched session carrying `f` | **decided** 2026-11-05: Brier 0.0788 vs continuous 0.0799 on 9,272 finished-market observations |
| `one_touch` | continuous one-touch (`maths.one_touch`) | the former rule; kept to compare (`lab score`) |
| `call_spread` | P(finish above K) from the out-of-the-money option spread around K, at the expiry closing exactly at the deadline | extension; no exact expiry = no price, never interpolated |

`regular_hours` parameters: `f` per stock from its daily bars known at the
cycle (`maths.intraday_variance_share`, at least `min_sessions` 60, clipped to
`f_clip` [0.05, 0.99], else `default_f` 0.6 - a guess; CLAUDE.md: the one
accepted input not from option prices); `paths` 20,000 antithetic draws,
`seed` 20260916, sized to the cycle's longest horizon.

A market it cannot price keeps `fair` NaN and says why in `why`; the gates
step rules it out.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import store
from pipeline import maths
from pipeline.core import Cycle, alive, sessions, variant
from pipeline.vol import expiry_closes


def _priceable(m: pd.DataFrame) -> pd.Series:
    return alive(m) & np.isfinite(m["sigma"].astype(float))


NO_VOL = "no vol: no fresh chain or no expiry after the deadline"


@variant("prob", "one_touch")
def one_touch(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    ok = _priceable(m)
    fair = np.full(len(m), np.nan)
    if ok.any():
        sub = m[ok]
        fair[ok.values] = maths.one_touch(sub["spot"].astype(float), sub["level"].astype(float),
                                          sub["T_years"].astype(float), sub["sigma"].astype(float),
                                          sub["rate"].astype(float), sub["yes_direction"].values)
    m["fair"] = fair
    m["why"] = np.where(ok, None, NO_VOL)
    return m


def intraday_share(ticker: str, as_of, p: dict) -> float:
    """`f` for one stock from the daily bars known at `as_of`."""
    default = p.get("default_f", 0.6)
    bars = store.daily_bars_asof(ticker, as_of)
    if bars is None or bars.empty:
        return default
    f = maths.intraday_variance_share(bars["open"].values, bars["close"].values,
                                      p.get("min_sessions", 60))
    lo, hi = p.get("f_clip", [0.05, 0.99])
    return default if not np.isfinite(f) else float(np.clip(f, lo, hi))


@variant("prob", "regular_hours")
def regular_hours(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    ok = _priceable(m)
    clock = cycle.memo("ticker_clock", lambda: m.groupby("ticker")["fetched_at_utc"].max())
    longest = cycle.memo("max_T_sessions", lambda: float(np.nanmax(m["T_sessions"])))
    pool = cycle.memo(("pool", p.get("paths", 20_000), p.get("seed", 20260916)),
                      lambda: maths.normal_pool(int(math.ceil(longest)) + 2,
                                                p.get("paths", 20_000), p.get("seed", 20260916)))
    shares = cycle.memo("intraday_share", dict)
    fair, f_used = [], []
    for row, priceable in zip(m.itertuples(), ok):
        if row.ticker not in shares:
            shares[row.ticker] = intraday_share(row.ticker, clock[row.ticker], p)
        f_used.append(shares[row.ticker])
        fair.append(maths.touch_probability(row.spot, row.level, row.T_sessions, row.sigma,
                                            row.rate, shares[row.ticker],
                                            row.contract_type == "hit_up", pool)
                    if priceable else np.nan)
    m["fair"], m["f"] = fair, f_used
    m["why"] = np.where(ok, None, NO_VOL)
    return m


# --------------------------------------------------------- from the chain

#: Index option roots that settle on the morning's opening prints. On a third
#: Friday they share an expiry date with SPXW / NDXP, which settle at the close.
MORNING_SETTLED_ROOTS = ("SPX", "NDX")


def settled_at_the_close(chain: pd.DataFrame) -> pd.DataFrame:
    """Drop morning-settled index options: the markets here observe the close."""
    roots = chain["contract_symbol"].str.extract(r"^([A-Z]+)", expand=False)
    return chain[~roots.isin(MORNING_SETTLED_ROOTS)]


def prob_above(options: pd.DataFrame, level: float, rate: float, years: float) -> float:
    """P(S_T > level) from the out-of-the-money spread around `level`.

        level at or above the money: e^{rT} (C(K1) - C(K2)) / (K2 - K1)
        level below the money:       1 - e^{rT} (P(K2) - P(K1)) / (K2 - K1)

    K1 / K2 the nearest listed strikes below / above the level (a strike at the
    level itself is skipped, so the spread is centred on it when strikes are
    evenly spaced); the money is the strike where calls and puts cost the most
    alike. Deep in-the-money mids are too wide to difference: priced from calls
    alone, far-below-the-money levels came out at exactly 1.000 and lost money.
    On a flat-vol chain this is N(d2); with strikes wide against the move to
    expiry it carries discretisation error. NaN with no strike on one side.
    """
    mids = options.dropna(subset=["mid"])
    calls = mids[mids["option_type"] == "call"].groupby("strike")["mid"].mean().sort_index()
    puts = mids[mids["option_type"] == "put"].groupby("strike")["mid"].mean().sort_index()
    both = calls.index.intersection(puts.index)
    if both.empty:
        return float("nan")
    use_calls = level >= (calls[both] - puts[both]).abs().idxmin()
    strikes = calls if use_calls else puts
    below = strikes[strikes.index < level]
    above = strikes[strikes.index > level]
    if below.empty or above.empty:
        return float("nan")
    k1, m1 = float(below.index[-1]), float(below.iloc[-1])
    k2, m2 = float(above.index[0]), float(above.iloc[0])
    growth = math.exp(rate * max(years, 0.0))
    q = growth * (m1 - m2) / (k2 - k1) if use_calls else 1.0 - growth * (m2 - m1) / (k2 - k1)
    return float(np.clip(q, 0.0, 1.0))


def _fair_yes(row, above) -> float | None:
    """P(outcome 0 wins) for one chain-priced market; None for a kind not priced."""
    kind = row.contract_type
    if kind == "close_above":
        q = above(row.strike)
        return 1.0 - q if row.direction == "below" else q
    if kind == "up_down":
        q = above(row.level)
        return q if row.outcome_0 == "Up" else 1.0 - q
    if kind == "range":
        low = row.range_low if pd.notna(row.range_low) else None
        high = row.range_high if pd.notna(row.range_high) else None
        if low is not None and high is not None:
            return above(low) - above(high)
        if low is not None:
            return above(low)
        if high is not None:
            return 1.0 - above(high)
        return float("nan")
    # Kalshi strike types. Kalshi writes its levels as 7574.9999; to the cent
    # that is 7575, the level the next bracket starts from.
    if kind in ("greater", "greater_or_equal"):
        return above(round(row.floor_strike, 2))
    if kind in ("less", "less_or_equal"):
        return 1.0 - above(round(row.cap_strike, 2))
    if kind == "between":
        return above(round(row.floor_strike, 2)) - above(round(row.cap_strike, 2))
    return None


@variant("prob", "call_spread")
def call_spread(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    stale_s = p.get("stale_chain_s", 1800.0)
    symbols = tuple(sorted(m["ticker"].dropna().unique()))
    chain = cycle.chain(symbols)
    rate = cycle.rate(m["fetched_at_utc"].max())
    fair, why = [], []
    for row, live in zip(m.itertuples(), alive(m)):
        if not live:
            fair.append(np.nan), why.append(None)
            continue
        mine = settled_at_the_close(chain[chain["underlying"] == row.ticker])
        if mine.empty or pd.isna(row.quote_time) or row.quote_age_s > stale_s:
            fair.append(np.nan), why.append("no fresh option chain")
            continue
        if row.quote_time > row.fetched_at_utc:
            # The fair would know something the book did not.
            fair.append(np.nan), why.append("option quote newer than the book")
            continue
        closes = cycle.memo(("closes", row.ticker), lambda mine=mine: expiry_closes(
            sorted(mine["expiry"].dropna().unique()), sessions()))
        expiry = next((e for e, c in closes.items() if pd.notna(c) and pd.notna(row.deadline)
                       and c.floor("min") == row.deadline.floor("min")), None)
        if expiry is None:
            fair.append(np.nan), why.append("no expiry at the deadline")
            continue
        options = mine[mine["expiry"] == expiry]
        q = _fair_yes(row, lambda k: prob_above(options, float(k), rate, row.years))
        if q is None:
            fair.append(np.nan), why.append("strike type not priced")
        elif not np.isfinite(q):
            fair.append(np.nan), why.append("no strikes either side of the level")
        else:
            fair.append(float(np.clip(q, 0.0, 1.0))), why.append(None)
    m["fair"], m["why"], m["rate"] = fair, why, rate
    return m
