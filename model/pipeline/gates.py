"""Step 5 - gates: which priced markets may be traded at all.

The `standard` variant applies the gates named in `drop`, in that order; each
rules out the markets it catches (`gate` = its reason). Available gates:

| gate | rules out | decided / open |
|---|---|---|
| `touch_pending` | spot already beyond the barrier, or a finished regular-hours bar has printed it since the market began being watched, or (from 2026-11-17) Tiingo's live day high/low has, for a market watched since today's open (fair set to 1) | decided (review 2026-09-18: a touch is a path; live price 2026-11-17 after MSFT 525 was touched seconds before the book) |
| `no_fair` | markets the prob step could not price (its `why`) | - |
| `deep_otm` | barrier more than `deep_otm_sd` (2.5) ATM standard moves away, or no vol | 2.5 is a guess (FINDINGS 5.2: fair 0.002-0.147 on one SPY barrier depending on the vol) |
| `earnings_gap` | an earnings date between the deadline and the expiry the vol came from | decided with the ATM rule: the expiry's vol carries a jump the barrier never sees |
| `earnings_inside` | an earnings date between the book and the deadline: until the report, the expiry's vol carries one jump that the touch formula spreads over every session, overstating touches of nearby barriers; after it, the market trades again | decided 2026-11-16 (the reporting stock only) |
| `quote_ahead_of_book` | chain prints newer than the book (`quote_age_s < 0`) | review 2026-09-16 |
| `stale_book` | books untouched for longer than `inputs.stale_book_s` | available, not used |
| `opening` | books read in the first `opening_minutes` (30) after the 9:30 ET open | decided 2026-11-16: option vols jump ~3x more per 15 min then, and replayed bets placed then lost (-2% vs +6% later) |
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import store
from pipeline import maths
from pipeline.core import Cycle, alive, drop, variant


def touch_pending(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.Series:
    if "touched" in m:                      # a variant that checks touches itself (wti)
        pending = m["touched"].fillna(False).astype(bool) & alive(m)
        m.loc[pending, "fair"] = 1.0
        return pending
    pending = []
    for row, live in zip(m.itertuples(), alive(m)):
        if not live:
            pending.append(False)
            continue
        up = row.contract_type == "hit_up"
        already = (row.spot >= row.level) if up else (row.spot <= row.level)
        if not already:
            # The venue watches from the later of the period start and the
            # market's creation (sessions.watch_start).
            start = store.watch_start_for(row.market_id, row.deadline, row.period)
            if pd.notna(start):
                already = bool(store.touched_between(row.ticker, row.level, up, start,
                                                     row.fetched_at_utc))
            # The live day high/low covers the session up to the book, which
            # finished bars miss; usable only if the market watched from the open.
            extreme = getattr(row, "day_high" if up else "day_low", float("nan"))
            if not already and pd.notna(start) and pd.notna(extreme) \
                    and start <= session_open(row.fetched_at_utc):
                already = bool(extreme >= row.level) if up else bool(extreme <= row.level)
        pending.append(bool(already))
    pending = pd.Series(pending, index=m.index)
    m.loc[pending, "fair"] = 1.0
    return pending


def session_open(when) -> pd.Timestamp:
    """09:30 ET on the day of `when`, in UTC."""
    local = pd.Timestamp(when).tz_convert("America/New_York")
    return local.normalize().replace(hour=9, minute=30).tz_convert("UTC")


def no_fair(m, cycle, p) -> pd.Series:
    return m["fair"].isna()


def deep_otm(m, cycle, p) -> pd.Series:
    sd = p.get("deep_otm_sd", 2.5)
    return pd.Series([maths.deep_otm(s, b, sig, t, threshold=sd) for s, b, sig, t in
                      zip(m["spot"], m["level"], m["sigma"], m["T_years"])], index=m.index)


def earnings_gap(m, cycle, p) -> pd.Series:
    return pd.Series([bool(pd.notna(close) and close > deadline
                           and store.earnings_between(t, deadline, close, as_of=fetched))
                      for t, deadline, close, fetched in
                      zip(m["ticker"], m["deadline"], m["expiry_close"], m["fetched_at_utc"])],
                     index=m.index)


def earnings_inside(m, cycle, p) -> pd.Series:
    return pd.Series([bool(store.earnings_between(t, pd.Timestamp(fetched), deadline,
                                                  as_of=fetched))
                      for t, deadline, fetched in
                      zip(m["ticker"], m["deadline"], m["fetched_at_utc"])], index=m.index)


def quote_ahead_of_book(m, cycle, p) -> pd.Series:
    return m["quote_age_s"] < 0


def stale_book(m, cycle, p) -> pd.Series:
    return m["stale_book"].fillna(False).astype(bool)


def opening(m, cycle, p) -> pd.Series:
    local = pd.to_datetime(m["fetched_at_utc"], utc=True).dt.tz_convert("America/New_York")
    minutes = local.dt.hour * 60 + local.dt.minute - (9 * 60 + 30)
    return pd.Series((minutes < p.get("opening_minutes", 30)).values, index=m.index)


GATES = {"touch_pending": touch_pending, "no_fair": no_fair, "deep_otm": deep_otm,
         "earnings_gap": earnings_gap, "earnings_inside": earnings_inside,
         "quote_ahead_of_book": quote_ahead_of_book,
         "stale_book": stale_book, "opening": opening}


@variant("gates", "standard")
def standard(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    for name in p["drop"]:
        mask = GATES[name](m, cycle, p) & alive(m)
        if name == "no_fair":
            reasons = m["why"].where(m["why"].notna(), "no fair")
            for reason in reasons[mask].unique():
                drop(m, mask & (reasons == reason), reason)
        else:
            drop(m, mask, name)
    m["tradeable"] = alive(m) & np.isfinite(m["fair"].astype(float))
    return m
