"""Step 2 - inputs: the spot, the clocks and the chain's age, per market.

| variant | what it sets | decided / open |
|---|---|---|
| `hit` | `spot` (open of the 5-minute bar containing the fetch; Cboe's delayed last if none), `T_sessions` / `T_years` (NYSE session time to the deadline), `quote_age_s` | decided (review 2026-09-18) |
| `chain` | `quote_time`, `years` (calendar years from the quote to the deadline), and for up/down the `level`: the previous trading day's close (matches the result on 98.7% of 760 resolved markets, 2026-11-04; today's open 74%); "Opens Up or Down" markets ruled out | extension |
| `wti` | see `wti.py` | extension |

`hit` refuses a cycle whose 5-minute bars have not reached it
(`require_bars`): without them the touch check sees nothing and calls every
barrier untouched (review 2026-10-09: 40 fills on barriers already printed).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import store
from pipeline.core import Cycle, NotReady, drop, sessions, variant
from sessions import TRADING_DAYS, sessions_remaining

BARS_PROBE = "SPY"                      # always collected; its bars stand for the day's
BARS_LAG = pd.Timedelta(minutes=10)     # bars are stamped at their start


def bars_cover(cycle_id: str) -> bool:
    """Have the 5-minute bars reached this cycle yet?"""
    bars = store.bars_5m()
    probe = bars.loc[bars["symbol"] == BARS_PROBE, "bar_time"]
    return bool(len(probe)) and probe.max() >= pd.Timestamp(store.cycle_start(cycle_id)) - BARS_LAG


@variant("inputs", "hit")
def hit(markets: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    if p.get("require_bars", True) and not bars_cover(cycle.id):
        raise NotReady(f"{cycle.id}: no 5-minute bars yet")
    m = markets
    m["spot"] = store.spot_at(m["fetched_at_utc"], m["ticker"])
    m["spot_source"] = np.where(m["spot"].notna(), "bar_5m", "cboe_delayed")
    m["spot"] = m["spot"].fillna(m["ticker"].map(cycle.cboe_spot()))
    m["T_sessions"] = sessions_remaining(m["fetched_at_utc"].values, m["deadline"].values,
                                         sessions())
    m["T_years"] = m["T_sessions"] / TRADING_DAYS
    quote_time = m["ticker"].map(cycle.quote_times())
    m["quote_time"] = pd.to_datetime(quote_time, utc=True)
    m["quote_age_s"] = (m["fetched_at_utc"] - m["quote_time"]).dt.total_seconds()
    m["stale_book"] = m["book_age_s"] > p.get("stale_book_s", 900.0)
    drop(m, ~(m["spot"] > 0) | ~(m["T_sessions"] > 0), "no spot or no time left")
    return m


@variant("inputs", "chain")
def chain(markets: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    m = markets
    m["quote_time"] = pd.to_datetime(m["ticker"].map(cycle.quote_times()), utc=True)
    m["quote_age_s"] = (m["fetched_at_utc"] - m["quote_time"]).dt.total_seconds()
    m["years"] = (m["deadline"] - m["quote_time"]).dt.total_seconds() / (365.0 * 86400.0)
    is_ud = m["contract_type"] == "up_down"
    if is_ud.any():
        # "X Opens Up or Down" compares the open with the previous close: decided
        # at 09:30 ET, and no chain expiring at the close prices it.
        opens = is_ud & m["event_slug"].astype(str).str.contains("opens-up-or-down")
        drop(m, opens, "decided at the open, not the close")
        m.loc[is_ud, "level"] = [previous_close(t, d) for t, d, u in
                                 zip(m["ticker"], m["deadline"], is_ud) if u]
        drop(m, is_ud & m["level"].isna(), "no previous close")
    return m


def previous_close(ticker: str, deadline: pd.Timestamp) -> float:
    """The last regular-session print before the deadline's own date."""
    bars = store.session_bars_5m(ticker)
    if bars.empty:
        return float("nan")
    eastern = bars["bar_time"].dt.tz_convert("America/New_York")
    before = bars[eastern.dt.date < deadline.tz_convert("America/New_York").date()]
    return float(before["close"].iloc[-1]) if len(before) else float("nan")
