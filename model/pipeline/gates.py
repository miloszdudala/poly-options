"""Step 5 - gates: which priced markets may be traded at all.

The `standard` variant applies the gates named in `drop`, in that order; each
rules out the markets it catches (`gate` = its reason). Available gates:

| gate | rules out | decided / open |
|---|---|---|
| `touch_pending` | spot already beyond the barrier, or a finished regular-hours bar has printed it since the market began being watched (fair set to 1) | decided (review 2026-09-18: a touch is a path) |
| `no_fair` | markets the prob step could not price (its `why`) | - |
| `deep_otm` | barrier more than `deep_otm_sd` (2.5) ATM standard moves away, or no vol | 2.5 is a guess (FINDINGS 5.2: fair 0.002-0.147 on one SPY barrier depending on the vol) |
| `earnings_gap` | an earnings date between the deadline and the expiry the vol came from | decided with the ATM rule: the expiry's vol carries a jump the barrier never sees |
| `quote_ahead_of_book` | chain prints newer than the book (`quote_age_s < 0`) | review 2026-09-16 |
| `stale_book` | books untouched for longer than `inputs.stale_book_s` | available, not used |
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
        pending.append(bool(already))
    pending = pd.Series(pending, index=m.index)
    m.loc[pending, "fair"] = 1.0
    return pending


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


def quote_ahead_of_book(m, cycle, p) -> pd.Series:
    return m["quote_age_s"] < 0


def stale_book(m, cycle, p) -> pd.Series:
    return m["stale_book"].fillna(False).astype(bool)


GATES = {"touch_pending": touch_pending, "no_fair": no_fair, "deep_otm": deep_otm,
         "earnings_gap": earnings_gap, "quote_ahead_of_book": quote_ahead_of_book,
         "stale_book": stale_book}


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
