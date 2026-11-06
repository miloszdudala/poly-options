"""Extension - WTI hit markets: the Active Month, the roll gap, and USO's vol at
the same moneyness (CLAUDE.md, Decided: WTI is priced from USO options, vol
converted at the same moneyness, with the contract-switch gap removed). The
variants `inputs: wti` and `vol: uso_same_moneyness` are at the end.

* **Active Month.** The markets resolve on ICE WTI "Active Month" (Pyth
  `CLL`). A contract stops being active at the open of its third-to-last
  session; its last session is 3 business days before the 25th calendar day of
  the month before delivery (4 if the 25th is not a business day). Business
  days here are NYSE sessions, which can differ from the ICE calendar on a few
  holidays. The ICE session opens at 18:00 ET the evening before.
* **Roll gap.** Our stored `WTI` bars are yfinance's front month (`CL=F`),
  which rolls at expiry, not at the Active Month switch, and the venue's
  series jumps by the gap between two contracts at the switch. The gap is
  removed by pricing on the contract that is Active at the deadline, at that
  contract's own price (yfinance `CL<month><yy>.NYM`, e.g. `CLF27.NYM`).
  Before a future switch the venue watches the earlier contract against the
  same barrier; that segment is priced as if on the later contract.
* **Vol.** USO's implied vol at the barrier's log-moneyness against WTI,
  ln(B / S_WTI), at the first USO expiry at or after the deadline, times
  `vol.vol_ratio`, the WTI / USO realised-vol ratio (measured 2026-10-27:
  median 1.06-1.09; which number is the user's to set - until it is, nothing
  is priced).
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

import store
from pipeline import maths
from pipeline.core import Cycle, alive, drop, sessions, variant
from pipeline.vol import expiry_closes

MONTH_CODES = "FGHJKMNQUVXZ"
EASTERN = "America/New_York"



def cl_symbol(year: int, month: int) -> str:
    """yfinance's symbol for the CL contract delivering in `year`-`month`."""
    return f"CL{MONTH_CODES[month - 1]}{year % 100:02d}.NYM"


def _business_days(sessions: pd.DataFrame) -> list[dt.date]:
    closes = pd.to_datetime(sessions["close"], utc=True).dt.tz_convert(EASTERN)
    return sorted(set(closes.dt.date))


def last_session(year: int, month: int, sessions: pd.DataFrame) -> dt.date:
    """The last trading day of the contract delivering in `year`-`month`."""
    prior_year, prior_month = (year, month - 1) if month > 1 else (year - 1, 12)
    the_25th = dt.date(prior_year, prior_month, 25)
    days = _business_days(sessions)
    before = [d for d in days if d < the_25th]
    back = 3 if the_25th in days else 4
    return before[-back]


def switch_time(year: int, month: int, sessions: pd.DataFrame) -> pd.Timestamp:
    """When the contract delivering in `year`-`month` stops being Active: the
    ICE open (18:00 ET the evening before) of its third-to-last session."""
    days = _business_days(sessions)
    third_to_last = days[days.index(last_session(year, month, sessions)) - 2]
    evening = dt.datetime.combine(third_to_last - dt.timedelta(days=1), dt.time(18, 0))
    return pd.Timestamp(evening, tz=EASTERN).tz_convert("UTC")


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year, month + 1) if month < 12 else (year + 1, 1)


def active_contract(when: pd.Timestamp, sessions: pd.DataFrame) -> tuple[int, int]:
    """The (year, month) of the contract that is Active Month at `when`."""
    local = pd.Timestamp(when).tz_convert(EASTERN)
    year, month = next_month(local.year, local.month)
    while switch_time(year, month, sessions) <= when:
        year, month = next_month(year, month)
    return year, month


def contract_to_price(now: pd.Timestamp, deadline: pd.Timestamp,
                      sessions: pd.DataFrame) -> tuple[int, int]:
    """The contract that is Active at the deadline: the one to price on."""
    return active_contract(max(now, deadline), sessions)


def vol_at_same_moneyness(smile, spot_wti: float, barrier: float,
                          ratio: float) -> tuple[float, bool]:
    """USO's calendar-clock implied vol at WTI's log-moneyness, times `ratio`.

    `smile` is a `fair.Smile` of USO, in log-moneyness against USO's own quote
    spot. Returns `(sigma, clamped)`.
    """
    sigma, clamped = smile.vol_at(math.log(barrier / spot_wti))
    return sigma * ratio, clamped


def in_ice_hours(times: pd.Series) -> pd.Series:
    """Inside ICE WTI trading: Sunday 18:00 to Friday 17:00 ET, less the daily
    17:00-18:00 ET break."""
    local = pd.to_datetime(times, utc=True).dt.tz_convert(EASTERN)
    hour, weekday = local.dt.hour, local.dt.weekday
    closed = ((hour == 17) | (weekday == 5) | ((weekday == 4) & (hour >= 17))
              | ((weekday == 6) & (hour < 18)))
    return ~closed


@variant("inputs", "wti")
def wti_inputs(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    """Spot on the contract Active at the deadline, touches in ICE hours, and
    calendar time to the raw `end_date` (WTI trades nearly round the clock)."""
    bars = store.bars_5m()
    m["deadline"] = m["end_date"]
    spots, touched, symbols = [], [], []
    for row, live in zip(m.itertuples(), alive(m)):
        year, month = contract_to_price(row.fetched_at_utc, row.deadline, sessions())
        symbol = cl_symbol(year, month)
        symbols.append(symbol)
        spot = float(store.spot_at(pd.Series([row.fetched_at_utc]), pd.Series([symbol])).iloc[0])
        spots.append(spot)
        touched.append(bool(live and np.isfinite(spot) and _touched(
            bars, symbol, row.level, row.contract_type == "hit_up", row.market_id,
            row.deadline, row.fetched_at_utc)))
    m["contract"], m["spot"], m["touched"] = symbols, spots, touched
    m["T_years"] = (m["deadline"] - m["fetched_at_utc"]).dt.total_seconds().clip(lower=0) \
        / (365.0 * 86400.0)
    m["quote_time"] = pd.to_datetime(pd.Series(["USO"] * len(m), index=m.index)
                                     .map(cycle.quote_times()), utc=True)
    m["quote_age_s"] = (m["fetched_at_utc"] - m["quote_time"]).dt.total_seconds()
    drop(m, ~(m["spot"] > 0), "no price for the contract Active at the deadline")
    return m


def _touched(bars, symbol, barrier, is_up, market_id, deadline, now) -> bool:
    start = store.watch_start_for(market_id, deadline, "monthly")
    mine = bars[bars["symbol"] == symbol]
    if mine.empty or pd.isna(start):
        return False
    mine = mine[(mine["bar_time"] >= start) & (mine["bar_time"] + pd.Timedelta(minutes=5) <= now)]
    mine = mine[in_ice_hours(mine["bar_time"]).values]
    return bool((mine["high"] >= barrier).any()) if is_up else bool((mine["low"] <= barrier).any())


@variant("vol", "uso_same_moneyness")
def uso_same_moneyness(m: pd.DataFrame, cycle: Cycle, p: dict) -> pd.DataFrame:
    ratio = p.get("vol_ratio")
    m["sigma"], m["expiry_close"] = np.nan, pd.NaT
    if ratio is None:
        drop(m, pd.Series(True, index=m.index), "WTI/USO vol ratio not set (the user's to set)")
        return m
    chain = cycle.chain(("USO",))
    uso_spot = cycle.cboe_spot().get("USO")
    m["rate"] = cycle.rate(m["fetched_at_utc"].max())
    closes = expiry_closes(sorted(chain["expiry"].dropna().unique()), sessions()) if len(chain) else {}
    sigmas = []
    for row, live in zip(m.itertuples(), alive(m)):
        sigma = np.nan
        if live and len(chain) and pd.notna(row.quote_time) and uso_spot is not None \
                and row.quote_age_s <= p.get("stale_chain_s", 1800.0) and row.quote_time <= row.fetched_at_utc:
            after = sorted(c for c in closes.values() if pd.notna(c) and c >= row.deadline)
            if after:
                expiry = next(e for e, c in closes.items() if c == after[0])
                years = (after[0] - row.quote_time).total_seconds() / (365.0 * 86400.0)
                smile = maths.build_smile(chain, float(uso_spot), expiry, years, years, m["rate"].iloc[0])
                sigma, _ = vol_at_same_moneyness(smile, row.spot, row.level, ratio)
        sigmas.append(sigma if np.isfinite(sigma) and sigma > 0 else np.nan)
    m["sigma"] = sigmas
    return m
