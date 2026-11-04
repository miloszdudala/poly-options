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
import time

import numpy as np
import pandas as pd

import data
import fair as fairlib
from sessions import (TRADING_DAYS, deadline_close, nyse_sessions,
                      sessions_remaining)
from util import get_logger

log = get_logger("model.engine")

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
    the touch formula runs on. See `fair.Smile`.
    """
    if pd.isna(close):
        return float("nan"), float("nan")
    anchor = book_time if pd.isna(quote_time) else quote_time
    years_cal = (close - anchor).total_seconds() / (365.0 * 86400.0)
    years_model = float(sessions_remaining(
        np.array([np.datetime64(book_time.tz_convert(None))]),
        np.array([np.datetime64(close.tz_convert(None))]), sess)[0]) / TRADING_DAYS
    return years_cal, years_model


def price_cycle(cycle_id: str) -> tuple[pd.DataFrame, dict]:
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

    smiles: dict[tuple, fairlib.Smile] = {}
    closes_by_ticker: dict[str, dict] = {}
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()

    rows = []
    for market in markets.itertuples():
        ticker = market.ticker
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
            "spot": spot, "spot_source": market.spot_source, "rate": rate,
            "T_sessions": t_sessions, "T_years": t_years,
            "quote_age_s": quote_age, "book_age_s": float(market.book_age_s),
            "book_timestamp_utc": market.book_timestamp_utc.isoformat(),
            "quote_time_utc": q_time.isoformat() if pd.notna(q_time) else None,
        }

        if not (np.isfinite(spot) and spot > 0) or t_sessions <= 0:
            flags.set("no_chain", True)
            rows.append({**row, **flags.columns()})
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
            rows.append({**row, "fair": 1.0, "delta": 0.0, **flags.columns()})
            continue

        # --- the vol: ATM, of the first expiry at or after the deadline ---
        sym_chain = chain[chain["underlying"] == ticker]
        if np.isfinite(quote_age) and quote_age > STALE_CHAIN_S:
            sym_chain = sym_chain.iloc[0:0]
        s_quote = _quote_spot(ticker, cboe, spot)
        if ticker not in closes_by_ticker:
            closes_by_ticker[ticker] = (expiry_closes(
                sorted(sym_chain["expiry"].dropna().unique()), sess) if len(sym_chain) else {})
        closes = closes_by_ticker[ticker]
        expiry_close = fairlib.first_expiry_at_or_after(
            [c for c in closes.values() if pd.notna(c)], market.deadline)
        expiry = {c: e for e, c in closes.items() if pd.notna(c)}.get(expiry_close)
        sigma_atm = float("nan")
        n_strikes = 0
        if expiry is not None:
            key = (ticker, expiry)
            if key not in smiles:
                cal, model_years = _clocks(expiry_close, q_time, reference_times[ticker], sess)
                smiles[key] = fairlib.build_smile(sym_chain, s_quote, expiry, cal,
                                                  model_years, rate, note_iv_gap)
            smile = smiles[key]
            n_strikes = smile.n_contracts
            sigma_atm, clamped = fairlib.expiry_vol(smile, 0.0)
            flags.set("clamped_atm", clamped)
            # A weekly barrier deadlines at the Friday close, which IS the
            # Friday expiry: the option matches the payoff's horizon exactly.
            flags.set("exact_expiry", expiry_close == market.deadline)
            # The earnings gap: a report in [deadline, expiry) sits inside the
            # expiry's variance and outside the barrier's window, so the fair
            # is biased high there and the signal does not trade it
            # (sim.candidate_edges). The MU monthly on 2026-09-16 is the case -
            # earnings 30 Sep after the bell, deadline 30 Sep close, priced off
            # the 2 Oct expiry.
            flags.set("earnings_gap", expiry_close > market.deadline
                      and data.earnings_between(ticker, market.deadline, expiry_close,
                                                as_of=market.fetched_at_utc))
        flags.set("no_chain", not np.isfinite(sigma_atm))
        flags.set("deep_otm", fairlib.deep_otm(spot, barrier, sigma_atm, t_years))

        fair = delta = float("nan")
        if np.isfinite(sigma_atm):
            direction = "up" if is_up else "down"
            # Continuous monitoring, although the venue watches regular-hours
            # candles only. A regular-hours correction would go here. Measured
            # 2026-10-30 on 3,326 markets: it moved fair by a median 1.4c
            # (p90 5.3c) and did not improve the Brier score (0.0941 with it,
            # 0.0945 without), so it is not applied (CLAUDE.md, Decided).
            fair = float(fairlib.one_touch(spot, barrier, t_years, sigma_atm, rate, direction))
            delta = fairlib.one_touch_delta(spot, barrier, t_years, sigma_atm, rate, direction)

        rows.append({**row, "fair": fair, "delta": delta, "sigma_atm": sigma_atm,
                     "expiry": str(expiry.date()) if expiry is not None else None,
                     "n_strikes": n_strikes, **flags.columns()})

    frame = pd.DataFrame(rows)
    gaps = np.asarray(iv_gaps, dtype=float)
    stats = {
        "cycle_id": cycle_id,
        "markets": len(frame),
        "priced": int(frame["fair"].notna().sum()) if "fair" in frame else 0,
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


def _quote_spot(ticker: str, cboe: pd.Series, spot: float) -> float:
    """The spot the IV surface is anchored on: Cboe's own, at the quote time.

    MODELS 3.2: the surface is built at `t_quote` with `S_quote`, then reused at
    `t_book` (sticky-strike over 15 minutes). Anchoring the smile on the
    15-minute-newer bar spot would shift every strike's moneyness by the drift
    since the quote and read it off the wrong part of the curve. The barrier's
    own moneyness is measured against the same `S_quote` for the same reason;
    the drift then enters exactly once, where it belongs, as the starting spot
    of the touch formula.
    """
    quoted = cboe.get(ticker)
    return float(quoted) if quoted is not None and np.isfinite(quoted) else float(spot)
