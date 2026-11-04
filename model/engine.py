"""Price every hit market in one cycle: the loop that joins the pieces together.

`fair.py` holds the maths and knows nothing about the store; `data.py` holds
the store and knows nothing about the maths. This is where they meet, and it is
where the three alignment traps of MODELS 3.1 get handled exactly once:

* the **spot** is the last print known at `fetched_at_utc` - the open of the
  5-minute bar containing it - not the Cboe last price, which is 15 minutes
  stale (fallback, flagged `spot_source='cboe_delayed'`). It is the fetch
  time, not `book_timestamp_utc`: the latter is the venue's last change to the
  book and can be days old, and a resting order is executable at the moment
  it was seen, so that is the moment its fair value is for;
* the **quote time** is `max(last_trade_time_utc)` per symbol, never
  `source_quote_time_utc`, and the gap between it and the book is carried as
  `quote_age_s` rather than assumed away;
* the **deadline** is the last NYSE session close at or before `end_date`,
  because a barrier can only print during regular hours.

A market whose spot is already beyond its barrier, **or whose barrier has
printed in regular hours since the market began being watched** (the later of
the period start and its own creation, `sessions.watch_start`), is skipped for pricing and
flagged `touch_pending` (MODELS 3.1): its fair value is 1 by arithmetic and
the only thing a model could learn from it is how late the venue is. The
second half of that sentence is the review fix of 2026-09-18: a touch is a
path, not a level, and seven of `edge_taker`'s ten resolved NO positions on
2026-09-16 were bought after the barrier had already printed.
"""
from __future__ import annotations

import datetime as dt
import math
import time

import numpy as np
import pandas as pd

import data
import empirical
import fair as fairlib
from sessions import (TRADING_DAYS, deadline_close, nyse_sessions,
                      sessions_remaining)
from util import get_logger

log = get_logger("model.engine")

DEFAULT_F = 0.6          # MODELS 3.3 expects 0.5-0.7 for single names
STALE_BOOK_S = 900.0     # a book untouched for longer than one cycle is a fossil
# Cboe's chain is 15 minutes delayed, so for the first ~15-30 minutes of a
# session its latest prints are the previous day's (2026-09-18 13:30Z and
# 13:45Z: median quote age 63,015 s and 63,927 s, i.e. yesterday's close).
# Yesterday's smile against today's spot is not a fair value; past this age the
# chain is treated as absent and the market is left unpriced.
STALE_CHAIN_S = 1800.0


def expiry_closes(expiries, sess: pd.DataFrame) -> dict:
    """Each expiry date mapped to its 16:00 ET session close, in UTC.

    An option expiring on Friday and a weekly barrier deadlining on Friday end
    at the same instant; comparing an expiry's midnight against a deadline's
    close would put them on opposite sides of it.
    """
    out = {}
    for expiry in expiries:
        stamp = pd.Timestamp(expiry)
        naive = stamp.tz_convert(None) if stamp.tz is not None else stamp
        close = deadline_close(np.array([np.datetime64(naive + pd.Timedelta(hours=23))]),
                               sess)[0]
        out[expiry] = pd.Timestamp(close, tz="UTC") if not np.isnat(close) else pd.NaT
    return out


def _clocks(close: pd.Timestamp, quote_time, book_time: pd.Timestamp,
            sess: pd.DataFrame) -> tuple[float, float]:
    """(calendar years from the quote, session years from the book) to `close`.

    The first is the clock Cboe's quoted vol lives on, the second is the clock
    the touch model runs on. See `fair.Smile`.
    """
    if pd.isna(close):
        return float("nan"), float("nan")
    anchor = book_time if pd.isna(quote_time) else quote_time
    years_cal = (close - anchor).total_seconds() / (365.0 * 86400.0)
    years_model = float(sessions_remaining(
        np.array([np.datetime64(book_time.tz_convert(None))]),
        np.array([np.datetime64(close.tz_convert(None))]), sess)[0]) / TRADING_DAYS
    return years_cal, years_model


class TickerContext:
    """Everything about one ticker that does not change within a cycle.

    `reference_time` is one book timestamp for the whole ticker, not each
    market's own. The markets of a ticker are booked seconds apart, which
    changes nothing about the smile but would change which market's clock the
    cached smile was built on - so the answer would depend on the order the
    rows came back from DuckDB. It is a per-ticker constant here, and pricing
    is reproducible because of it.
    """

    def __init__(self, ticker: str, reference_time: pd.Timestamp):
        self.reference_time = reference_time
        # `daily_bars_asof`, not `bar_time < reference_time`: a daily bar is
        # stamped at midnight Eastern, so the naive filter handed a 14:00Z
        # cycle its own day's 16:00 ET close (P6). `f` takes the same cut.
        past = data.daily_bars_asof(ticker, reference_time)
        self.f = empirical.estimate_intraday_share(ticker, DEFAULT_F,
                                                   as_of=reference_time)
        self.sigma_realised = float("nan")
        if len(past) > 60:
            self.sigma_realised = float(empirical.ewma_vol(past["close"].values)[-1])


def price_cycle(cycle_id: str, paths: int = fairlib.DEFAULT_PATHS,
                seed: int = 20260916) -> tuple[pd.DataFrame, dict]:
    """One row per (cycle_id, market_id) for every hit market in the cycle."""
    started = time.time()
    sess = nyse_sessions()
    markets = data.market_book_times(cycle_id)
    if markets.empty:
        return pd.DataFrame(), {"cycle_id": cycle_id, "markets": 0}

    markets = markets.dropna(subset=["barrier", "end_date", "book_timestamp_utc",
                                     "fetched_at_utc"])
    markets = markets[markets["barrier"] > 0].reset_index(drop=True)
    # The cycle's clock is when the book was seen. `book_timestamp_utc` is when
    # the venue last changed it, which P2 measured at up to 4.9 days old.
    markets["spot"] = data.spot_at(markets["fetched_at_utc"], markets["ticker"])
    markets["spot_source"] = np.where(markets["spot"].notna(), "bar_5m", "cboe_delayed")

    cboe = data.cboe_spot(cycle_id).set_index("symbol")["last_price"]
    fallback = markets["ticker"].map(cboe)
    markets["spot"] = markets["spot"].fillna(fallback)

    deadlines = deadline_close(markets["end_date"].values.astype("datetime64[ns]"), sess)
    markets["deadline"] = pd.to_datetime(deadlines, utc=True)
    markets["T_sessions"] = sessions_remaining(
        markets["fetched_at_utc"].values, markets["deadline"].values, sess)
    markets["T_years"] = markets["T_sessions"] / TRADING_DAYS

    symbols = tuple(sorted(markets["ticker"].dropna().unique()))
    chain = data.option_chain(cycle_id, symbols)
    quote_time = data.quote_times(cycle_id)
    asof = markets["fetched_at_utc"].max()
    rate = data.risk_free_rate(asof)
    # One clock per ticker, so a cached smile does not depend on which of that
    # ticker's markets asked for it first (they are fetched seconds apart).
    reference_times = markets.groupby("ticker")["fetched_at_utc"].max()

    iv_gaps: list[float] = []

    worst_iv: list[tuple] = []

    def note_iv_gap(symbols_, mine, theirs):
        """MODELS 3.2: verify our inversion against Cboe's on liquid strikes."""
        gaps = np.abs(np.asarray(mine) - np.asarray(theirs))
        iv_gaps.extend(gaps.tolist())
        bad = gaps > 0.01
        for symbol, ours, cboe_iv in zip(np.asarray(symbols_)[bad], np.asarray(mine)[bad],
                                         np.asarray(theirs)[bad]):
            worst_iv.append((str(symbol), float(ours), float(cboe_iv)))

    contexts: dict[str, TickerContext] = {}
    smiles: dict[tuple, fairlib.Smile] = {}
    closes_by_ticker: dict[str, dict] = {}
    # Sized from the cycle's own longest horizon: the SPX year-end ladders run
    # ~70 sessions out, a weekly barrier three, and a fixed guess is either
    # wasteful or an IndexError halfway through a run.
    max_legs = int(math.ceil(float(np.nanmax(markets["T_sessions"])))) + 2
    pool = fairlib.normal_pool(max_legs, paths=paths, seed=seed)
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()

    rows = []
    for market in markets.itertuples():
        ticker = market.ticker
        if ticker not in contexts:
            contexts[ticker] = TickerContext(ticker, reference_times[ticker])
        ctx = contexts[ticker]
        flags = fairlib.Flags()

        spot, barrier = market.spot, float(market.barrier)
        is_up = market.contract_type == "hit_up"
        t_sessions = float(market.T_sessions)
        t_years = float(market.T_years)
        q_time = quote_time.get(ticker, pd.NaT)
        quote_age = ((market.fetched_at_utc - q_time).total_seconds()
                     if pd.notna(q_time) else float("nan"))
        flags.set("stale_book", float(market.book_age_s) > STALE_BOOK_S)
        # [fetch, deadline): a report stamped at the deadline's own close is
        # after the last candle that could print the barrier (see data).
        flags.set("earnings_inside",
                  data.earnings_between(ticker, market.fetched_at_utc, market.deadline,
                                        as_of=market.fetched_at_utc))

        row = {
            "cycle_id": cycle_id, "computed_at_utc": computed_at,
            "market_id": str(market.market_id), "ticker": ticker,
            "contract_type": market.contract_type,
            "direction": "above" if is_up else "below",
            "barrier": barrier, "period": market.period,
            "end_date": market.end_date.isoformat() if pd.notna(market.end_date) else None,
            "deadline_utc": market.deadline.isoformat() if pd.notna(market.deadline) else None,
            "f": ctx.f, "sigma_realised": ctx.sigma_realised,
            "spot": spot, "spot_source": market.spot_source, "rate": rate,
            "T_sessions": t_sessions, "T_years": t_years,
            "quote_age_s": quote_age, "book_age_s": float(market.book_age_s),
            "book_timestamp_utc": market.book_timestamp_utc.isoformat(),
            "quote_time_utc": q_time.isoformat() if pd.notna(q_time) else None,
        }

        if not (np.isfinite(spot) and spot > 0) or t_sessions <= 0:
            flags.set("no_chain", True)
            rows.append({**row, "flags": flags.as_string(), **_flag_dict(flags)})
            continue

        already = (spot >= barrier) if is_up else (spot <= barrier)
        # A touch is a path. The venue watches every regular-hours candle from
        # the later of the period start and the market's creation; a barrier
        # that printed this morning is a pending YES this afternoon however far
        # the spot has bounced, but one that printed before the market was
        # listed never counts (sessions.watch_start).
        watched_from = data.watch_start_for(market.market_id, market.deadline, market.period)
        if not already and pd.notna(watched_from):
            already = bool(data.touched_between(ticker, barrier, is_up, watched_from,
                                                market.fetched_at_utc))
        if already:
            flags.set("touch_pending", True)
            rows.append({**row, "fair_K": 1.0, "fair_atm": 1.0, "fair_cont": 1.0,
                         "delta": 0.0,
                         "flags": flags.as_string(), **_flag_dict(flags)})
            continue

        # --- option-implied: two vols, two expiries, one deadline ---
        sym_chain = chain[chain["underlying"] == ticker]
        if np.isfinite(quote_age) and quote_age > STALE_CHAIN_S:
            sym_chain = sym_chain.iloc[0:0]
        s_quote = _quote_spot(ticker, cboe, spot)
        if ticker not in closes_by_ticker:
            closes_by_ticker[ticker] = (expiry_closes(
                sorted(sym_chain["expiry"].dropna().unique()), sess) if len(sym_chain) else {})
        closes = closes_by_ticker[ticker]
        after_close, before_close = fairlib.pick_expiries(
            [c for c in closes.values() if pd.notna(c)], market.deadline)
        by_close = {c: e for e, c in closes.items() if pd.notna(c)}
        after = by_close.get(after_close)
        before = by_close.get(before_close)
        sigma_k = sigma_atm = float("nan")
        vol_source = "none"
        n_after = n_before = 0
        if after is not None or before is not None:
            built = []
            for expiry, close in ((before, before_close), (after, after_close)):
                if expiry is None:
                    built.append(None)
                    continue
                key = (ticker, expiry)
                if key not in smiles:
                    cal, model_years = _clocks(close, q_time, ctx.reference_time, sess)
                    smiles[key] = fairlib.build_smile(sym_chain, s_quote, expiry, cal,
                                                      model_years, rate, note_iv_gap)
                built.append(smiles[key])
            s0, s1 = built
            n_before = s0.n_contracts if s0 else 0
            n_after = s1.n_contracts if s1 else 0
            # The earnings gap: a report in [deadline, expiry_after) sits inside
            # the after-expiry's variance and outside the barrier's window. The
            # MU monthly on 2026-09-16 is the case - earnings 30 Sep after the
            # bell, deadline 30 Sep close, priced off the 2 Oct expiry. Where a
            # pre-earnings expiry exists its vol is carried flat instead; where
            # none does the fair is flagged and the option signal must not
            # trade it (sim.candidate_edges).
            gap = (after_close is not None and pd.notna(after_close)
                   and after_close > market.deadline
                   and data.earnings_between(ticker, market.deadline, after_close,
                                             as_of=market.fetched_at_utc))
            flags.set("earnings_gap", gap)
            if gap and s0 is not None:
                sigma_atm, clamp_atm, vol_source = fairlib.flat_vol(s0, 0.0)
                sigma_k, clamp_k, _ = fairlib.flat_vol(s0, math.log(barrier / s_quote))
            else:
                sigma_atm, clamp_atm, vol_source = fairlib.interp_vol(s0, s1, t_years, 0.0)
                sigma_k, clamp_k, _ = fairlib.interp_vol(s0, s1, t_years,
                                                         math.log(barrier / s_quote))
            flags.set("clamped_atm", clamp_atm)
            flags.set("clamped_strike", clamp_k)
            flags.set("single_expiry", vol_source == "single_expiry")
            # A weekly barrier deadlines at the Friday close, which IS the Friday
            # expiry. There is then nothing to interpolate and nothing lost by it:
            # the option matches the payoff's horizon exactly. Without this flag a
            # model reading `single_expiry` as a warning would throw away its
            # best-matched markets.
            flags.set("exact_expiry", after_close is not None
                      and pd.notna(after_close) and after_close == market.deadline)
        flags.set("no_chain", not np.isfinite(sigma_k) and not np.isfinite(sigma_atm))
        flags.set("deep_otm", fairlib.deep_otm(spot, barrier, sigma_atm, t_years))

        fair_k = fair_atm = fair_cont = delta = float("nan")
        if np.isfinite(sigma_k):
            fair_k = fairlib.touch_probability(spot, barrier, t_sessions, sigma_k,
                                               rate, ctx.f, is_up, pool)
            fair_cont = float(fairlib.one_touch(spot, barrier, t_years, sigma_k, rate,
                                                "up" if is_up else "down"))
            delta = fairlib.touch_delta(spot, barrier, t_sessions, sigma_k, rate,
                                        ctx.f, is_up, pool)
        if np.isfinite(sigma_atm):
            fair_atm = fairlib.touch_probability(spot, barrier, t_sessions, sigma_atm,
                                                 rate, ctx.f, is_up, pool)

        rows.append({**row, "fair_K": fair_k, "fair_atm": fair_atm, "fair_cont": fair_cont,
                     "delta": delta,
                     "sigma_K": sigma_k, "sigma_atm": sigma_atm,
                     "spread_iv": abs(sigma_k - sigma_atm)
                     if np.isfinite(sigma_k) and np.isfinite(sigma_atm) else float("nan"),
                     "expiry_after": str(after.date()) if after is not None else None,
                     "expiry_before": str(before.date()) if before is not None else None,
                     "vol_source": vol_source,
                     "n_strikes_after": n_after, "n_strikes_before": n_before,
                     "flags": flags.as_string(), **_flag_dict(flags)})

    frame = pd.DataFrame(rows)
    gaps = np.asarray(iv_gaps, dtype=float)
    stats = {
        "cycle_id": cycle_id,
        "markets": len(frame),
        "priced": int(frame["fair_K"].notna().sum()) if "fair_K" in frame else 0,
        "seconds": round(time.time() - started, 1),
        "median_quote_age_s": float(np.nanmedian(frame["quote_age_s"])) if len(frame) else float("nan"),
        "iv_checked": int(len(gaps)),
        "iv_disagreements": int((gaps > 0.01).sum()),
        "iv_gap_p90": float(np.nanquantile(gaps, 0.9)) if len(gaps) else float("nan"),
        "iv_gap_median": float(np.nanmedian(gaps)) if len(gaps) else float("nan"),
    }
    if worst_iv:
        worst = sorted(worst_iv, key=lambda r: -abs(r[1] - r[2]))[:5]
        log.warning("IV disagrees with Cboe past 0.01 on %d/%d liquid strikes; worst: %s",
                    len(worst_iv), len(gaps),
                    ", ".join(f"{s} ours={o:.3f} cboe={c:.3f}" for s, o, c in worst))
    return frame, stats


def _flag_dict(flags: fairlib.Flags) -> dict:
    return {name: getattr(flags, name) for name in
            ("deep_otm", "clamped_strike", "clamped_atm", "single_expiry",
             "exact_expiry", "no_chain", "stale_book", "earnings_inside",
             "earnings_gap", "touch_pending")}


def _quote_spot(ticker: str, cboe: pd.Series, spot: float) -> float:
    """The spot the IV surface is anchored on: Cboe's own, at the quote time.

    MODELS 3.2: the surface is built at `t_quote` with `S_quote`, then reused at
    `t_book` (sticky-strike over 15 minutes). Anchoring the smile on the
    15-minute-newer bar spot would shift every strike's moneyness by the drift
    since the quote and read it off the wrong part of the curve. The barrier's
    own moneyness is measured against the same `S_quote` for the same reason;
    the drift then enters exactly once, where it belongs, as the starting spot
    of the touch model.
    """
    quoted = cboe.get(ticker)
    return float(quoted) if quoted is not None and np.isfinite(quoted) else float(spot)
