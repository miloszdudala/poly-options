"""Step 3 - vol: which implied volatility goes into the probability.

| variant | vol | decided / open |
|---|---|---|
| `strike_first_expiry_after` | the vol at the barrier's moneyness of the first expiry whose 16:00 ET close is at or after the deadline; with `min_rel_volume`, only from serious strikes (`maths.barrier_vol`, `max_gap`) | **decided** 2026-11-14 (the MVP) |
| `atm_first_expiry_after` | the same expiry's at-the-money vol | replaced 2026-11-14: it ignores the skew; kept to compare |
| `none` | no vol (the chain-priced types read the call spread directly) | extension |

Shared choices (parameters): a chain older than `stale_chain_s` (1,800 s) is
treated as absent - yesterday's smile against today's spot is not a fair
value. Contracts are Cboe's, `open_interest >= 100` or `volume > 0`, `bid > 0`
(`store.option_chain`); IV is recomputed from the mid on OTM options
(`maths.build_smile`) and taken flat outside the liquid strikes. The quoted
vol is moved to the session clock by total variance (`maths.expiry_vol`).

Columns set: `sigma`, `expiry`, `expiry_close`, `exact_expiry`, `clamped`,
`n_strikes`, `s_quote`; and for `prob.variance: per_segment`, the quote itself
before any clock change: `vol_cal` (calendar-clock vol), `years_cal` and
`quote_sessions` (NYSE sessions from the quote to the expiry close); with
`before_minutes`, `vol_before`: the same market's barrier vol from the chain
that many minutes earlier (for the `vol_jump` gate); and for
`prob.local_vol: derman`, `vol_atm_cal`: the calendar-clock vol at the money
(log-moneyness 0) read off the same curve by the same rule as the barrier's.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import store
from pipeline import maths
from pipeline.core import Cycle, alive, sessions, variant
from sessions import TRADING_DAYS, deadline_close, sessions_remaining


def expiry_closes(expiries, sess: pd.DataFrame) -> dict:
    """Each expiry date mapped to its 16:00 ET session close, in UTC: a Friday
    expiry and a Friday deadline end at the same instant."""
    out = {}
    for expiry in expiries:
        stamp = pd.Timestamp(expiry)
        naive = stamp.tz_convert(None) if stamp.tz is not None else stamp
        close = deadline_close(np.array([np.datetime64(naive + pd.Timedelta(hours=23))]), sess)[0]
        out[expiry] = pd.Timestamp(close, tz="UTC") if not np.isnat(close) else pd.NaT
    return out


def _clocks(close: pd.Timestamp, quote_time, book_time: pd.Timestamp) -> tuple[float, float]:
    """(calendar years from the quote, session years from the book) to `close`:
    the clock Cboe's vol is quoted on, and the clock the touch formula runs on."""
    if pd.isna(close):
        return float("nan"), float("nan")
    anchor = book_time if pd.isna(quote_time) else quote_time
    years_cal = (close - anchor).total_seconds() / (365.0 * 86400.0)
    years_model = float(sessions_remaining(
        np.array([np.datetime64(book_time.tz_convert(None))]),
        np.array([np.datetime64(close.tz_convert(None))]), sessions())[0]) / TRADING_DAYS
    return years_cal, years_model


def _quote_sessions(close: pd.Timestamp, quote_time, book_time: pd.Timestamp) -> float:
    """NYSE sessions, fractional, from the quote to `close`: the span the
    quote's total variance covers, counted the way the simulation's legs are."""
    if pd.isna(close):
        return float("nan")
    anchor = book_time if pd.isna(quote_time) else quote_time
    return float(sessions_remaining(np.array([np.datetime64(anchor.tz_convert(None))]),
                                    np.array([np.datetime64(close.tz_convert(None))]),
                                    sessions())[0])


NO_SERIOUS = "no vol: no serious-volume strikes close around the barrier"


def _first_expiry_vol(m: pd.DataFrame, cycle: Cycle, p: dict, at_barrier: bool) -> pd.DataFrame:
    stale_s = p.get("stale_chain_s", 1800.0)
    symbols = tuple(sorted(m["ticker"].dropna().unique()))
    chain = cycle.chain(symbols)
    cboe = cycle.cboe_spot()
    clock = cycle.memo("ticker_clock", lambda: m.groupby("ticker")["fetched_at_utc"].max())
    rate = cycle.rate(m["fetched_at_utc"].max())
    closes_by_ticker = cycle.memo("closes_by_ticker", dict)
    smiles = cycle.memo("smiles", dict)
    spans = cycle.memo("quote_sessions", dict)
    out = {c: [] for c in ("sigma", "expiry", "expiry_close", "exact_expiry", "clamped",
                           "n_strikes", "s_quote", "vol_why", "vol_cal", "years_cal",
                           "quote_sessions", "vol_atm_cal")}
    for row, live in zip(m.itertuples(), alive(m)):
        values = dict(sigma=np.nan, expiry=None, expiry_close=pd.NaT, exact_expiry=False,
                      clamped=False, n_strikes=0, s_quote=np.nan, vol_why=None,
                      vol_cal=np.nan, years_cal=np.nan, quote_sessions=np.nan,
                      vol_atm_cal=np.nan)
        if live:
            ticker = row.ticker
            sym_chain = chain[chain["underlying"] == ticker]
            if np.isfinite(row.quote_age_s) and row.quote_age_s > stale_s:
                sym_chain = sym_chain.iloc[0:0]
            quoted = cboe.get(ticker)
            s_quote = float(quoted) if quoted is not None and np.isfinite(quoted) else float(row.spot)
            if ticker not in closes_by_ticker:
                closes_by_ticker[ticker] = (expiry_closes(sorted(sym_chain["expiry"].dropna().unique()),
                                                          sessions()) if len(sym_chain) else {})
            closes = closes_by_ticker[ticker]
            close = maths.first_expiry_at_or_after([c for c in closes.values() if pd.notna(c)],
                                                   row.deadline)
            expiry = {c: e for e, c in closes.items() if pd.notna(c)}.get(close)
            values["s_quote"] = s_quote
            if expiry is not None:
                key = (ticker, expiry)
                if key not in spans:
                    spans[key] = _quote_sessions(close, row.quote_time, clock[ticker])
                if key not in smiles:
                    cal, model_years = _clocks(close, row.quote_time, clock[ticker])
                    smiles[key] = maths.build_smile(sym_chain, s_quote, expiry, cal,
                                                    model_years, rate)
                smile = smiles[key]
                x = math.log(row.level / s_quote) if at_barrier else 0.0
                sigma, clamped = maths.expiry_vol(smile, x)
                vol_cal, years_cal = smile.vol_at(x)[0], smile.years_cal
                vol_atm_cal = smile.vol_at(0.0)[0]
                if at_barrier and p.get("min_rel_volume"):
                    skey = (ticker, expiry, "serious")
                    if skey not in smiles:
                        cal, model_years = _clocks(close, row.quote_time, clock[ticker])
                        smiles[skey] = maths.build_smile(
                            sym_chain, s_quote, expiry, cal, model_years, rate,
                            min_rel_volume=p["min_rel_volume"])
                    serious = smiles[skey]
                    vol, clamped = maths.barrier_vol(serious, x, p.get("max_gap", 0.03))
                    vol_cal, years_cal = vol, serious.years_cal
                    vol_atm_cal = maths.barrier_vol(serious, 0.0, p.get("max_gap", 0.03))[0]
                    sigma = (math.sqrt(vol ** 2 * serious.years_cal / serious.years_model)
                             if np.isfinite(vol) and serious.years_model > 0 else float("nan"))
                    if not np.isfinite(sigma):
                        values["vol_why"] = NO_SERIOUS
                values.update(sigma=sigma, expiry=str(expiry.date()), expiry_close=close,
                              exact_expiry=bool(close == row.deadline), clamped=clamped,
                              n_strikes=smile.n_contracts, vol_cal=vol_cal,
                              years_cal=years_cal,
                              quote_sessions=spans[key], vol_atm_cal=vol_atm_cal)
        for k, v in values.items():
            out[k].append(v)
    for k, v in out.items():
        m[k] = v
    m["rate"] = rate
    return m


@variant("vol", "atm_first_expiry_after")
def atm_first_expiry_after(m, cycle, p):
    return _first_expiry_vol(m, cycle, p, at_barrier=False)


def earlier_cycle(cycle: Cycle, minutes: float, tolerance: float = 4.0) -> str | None:
    """The same day's cycle closest to `minutes` before this one, if one lies
    within `tolerance` minutes of that time."""
    start = pd.Timestamp(store.cycle_start(cycle.id))
    want = start - pd.Timedelta(minutes=minutes)
    best, gap = None, pd.Timedelta(minutes=tolerance)
    for cid in store.session_cycles(cycle.date):
        off = abs(pd.Timestamp(store.cycle_start(cid)) - want)
        if cid < cycle.id and off <= gap:
            best, gap = cid, off
    return best


def vol_before(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.Series:
    """The barrier vol (calendar clock, as quoted) the chain `before_minutes`
    earlier gave each market, by the same rule; NaN where there is none.
    That chain's own quote times and spot are used, whatever its gates said."""
    prev_id = earlier_cycle(cycle, p["before_minutes"])
    if prev_id is None:
        return pd.Series(np.nan, index=m.index)
    prev = Cycle(prev_id, cycle.contract_types, cycle.venue)
    shift = pd.Timestamp(store.cycle_start(cycle.id)) - pd.Timestamp(store.cycle_start(prev_id))
    mb = m[["ticker", "level", "deadline", "spot", "fetched_at_utc", "gate"]].copy()
    mb["fetched_at_utc"] = mb["fetched_at_utc"] - shift
    mb["quote_time"] = pd.to_datetime(mb["ticker"].map(prev.quote_times()), utc=True)
    mb["quote_age_s"] = (mb["fetched_at_utc"] - mb["quote_time"]).dt.total_seconds()
    rest = {k: v for k, v in p.items() if k != "before_minutes"}
    return _first_expiry_vol(mb, prev, rest, at_barrier=True)["vol_cal"]


@variant("vol", "strike_first_expiry_after")
def strike_first_expiry_after(m, cycle, p):
    m = _first_expiry_vol(m, cycle, {k: v for k, v in p.items() if k != "before_minutes"},
                          at_barrier=True)
    if p.get("before_minutes"):
        m["vol_before"] = vol_before(m, cycle, p).values
    return m


@variant("vol", "none")
def no_vol(m, cycle, p):
    return m
