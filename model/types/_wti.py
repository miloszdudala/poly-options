"""WTI hit markets: the Active Month, the roll gap, and USO's vol at the same
moneyness (CLAUDE.md, Decided: WTI is priced from USO options, vol converted
at the same moneyness, with the contract-switch gap removed).

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
  `VOL_RATIO`, the WTI / USO realised-vol ratio.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

MONTH_CODES = "FGHJKMNQUVXZ"
EASTERN = "America/New_York"

#: WTI / USO realised-vol ratio. Measured 2026-10-27: median 1.06-1.09. Which
#: number (or a rolling measurement) is the user's to set; until it is, the
#: WTI type prices nothing and counts every market as skipped.
VOL_RATIO: float | None = None


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
