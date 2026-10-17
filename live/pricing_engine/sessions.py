"""NYSE session arithmetic, shared by the engine and the calibration report.

A hit market can only resolve on a Pyth candle between 09:30 and 16:00 ET
(MODELS 3.3), so every horizon in this project is counted in sessions, not in
calendar days. A Friday-to-Monday weekly barrier has three calendar days of
variance and one session of monitoring, and the two numbers are not
interchangeable.
"""
from __future__ import annotations

import functools

import numpy as np
import pandas as pd

TRADING_DAYS = 252.0


@functools.lru_cache(maxsize=8)
def nyse_sessions(start: str = "2025-12-01", end: str = "2026-12-31") -> pd.DataFrame:
    """Open and close of every NYSE session in the window, in UTC.

    `pandas_market_calendars` honours half days (the close is 13:00 ET on
    those) and holidays, which a hand-rolled 09:30-16:00 rule does not.
    """
    import pandas_market_calendars as mcal

    sched = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)
    return pd.DataFrame({
        "open": pd.to_datetime(sched["market_open"], utc=True).values,
        "close": pd.to_datetime(sched["market_close"], utc=True).values,
    })


def sessions_remaining(point_times: np.ndarray, deadlines: np.ndarray,
                       sess: pd.DataFrame | None = None) -> np.ndarray:
    """Monitored sessions between each point and its deadline, fractional.

    The session a point falls inside counts only for the part still to come: a
    point at 15:00 ET has 0.15 of that session left. An integer result means
    the point sits outside a session, so the next session begins with an
    overnight gap - which is exactly the distinction the Monte Carlo in
    `fair.touch_probability` needs.
    """
    sess = nyse_sessions() if sess is None else sess
    opens = sess["open"].values.astype("datetime64[ns]").astype(np.int64)
    closes = sess["close"].values.astype("datetime64[ns]").astype(np.int64)
    t = np.asarray(point_times).astype("datetime64[ns]").astype(np.int64)
    d = np.asarray(deadlines).astype("datetime64[ns]").astype(np.int64)

    first = np.searchsorted(closes, t, side="right")          # first session still open
    last = np.searchsorted(closes, d, side="right") - 1       # last session before deadline
    n = (last - first + 1).astype(float)
    inside = (first < len(opens)) & (first <= last)
    idx = np.clip(first, 0, len(opens) - 1)
    span = np.maximum(closes[idx] - opens[idx], 1)
    elapsed = np.clip((t - opens[idx]) / span, 0.0, 1.0)
    return np.where(inside, np.maximum(n - elapsed, 0.0), 0.0)


def period_start(deadline, period: str) -> pd.Timestamp:
    """The start of the market's period, in UTC: Monday 00:00 ET of the
    deadline's week, or the first of the deadline's month.

    This is only half of when a touch starts counting. The rules of every hit
    market created since 2026-08-07 add "after market creation"; use
    `watch_start`, which takes the later of the two. Read 2026-09-18 from older
    rules ("at any point during May 2026") and corrected 2026-09-23.
    A barrier that printed on Tuesday is a pending YES on Wednesday whatever
    the spot does afterwards, which is why the engine needs this and not just
    the current spot. NaT for a period this project does not trade.
    """
    stamp = pd.Timestamp(deadline)
    if pd.isna(stamp):
        return pd.NaT
    stamp = stamp.tz_localize("UTC") if stamp.tz is None else stamp
    eastern = stamp.tz_convert("America/New_York")
    if period == "monthly":
        start = eastern.replace(day=1)
    elif period == "weekly":
        start = eastern - pd.Timedelta(days=eastern.weekday())
    else:
        return pd.NaT
    start = start.replace(hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
    return start.tz_convert("UTC")


def watch_start(deadline, period: str, created_at=None, from_creation: bool = False) -> pd.Timestamp:
    """The first moment a touch counts for this market, in UTC.

    `period_start` alone is wrong for almost every market this project trades.
    Every hit market created since 2026-08-07 says "at any point **after market
    creation** and during the week of ..." (or "between market creation and
    ..."). Polymarket lists new barriers mid-week at ~12:37 ET, often at levels
    the stock already crossed earlier that week, and those earlier prints do
    not count: MU `hit_down 930`, created Thursday 2026-09-17 16:38Z, resolved
    NO although MU printed 902.60 on Monday. Measured 2026-09-23: 75 of 330
    markets the engine flagged touch_pending resolved NO, every one of them a
    barrier crossed only before its own creation.

    Only 1-minute candles after creation count; a bar that straddles the
    creation instant cannot say which side of it its high or low fell, so
    callers compare bar *starts* against this, which drops that bar.
    """
    start = period_start(deadline, period)
    if pd.isna(start) or not from_creation or created_at is None or pd.isna(created_at):
        return start
    created = pd.Timestamp(created_at)
    created = created.tz_localize("UTC") if created.tz is None else created.tz_convert("UTC")
    return max(start, created)


def deadline_close(end_dates: np.ndarray, sess: pd.DataFrame | None = None) -> np.ndarray:
    """The last session close at or before each `end_date`.

    A monthly ladder carries `end_date = 2026-10-01T03:59:59Z`, which is
    23:59 ET on 30 September - hours after the last candle anyone could touch a
    barrier on. The deadline that matters is that session's close.
    """
    sess = nyse_sessions() if sess is None else sess
    closes = sess["close"].values.astype("datetime64[ns]")
    idx = np.searchsorted(closes, np.asarray(end_dates).astype("datetime64[ns]"),
                          side="right") - 1
    out = np.full(len(idx), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    ok = idx >= 0
    out[ok] = closes[idx[ok]]
    return out
