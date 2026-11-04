"""Realised volatility and the intraday variance share, from `underlying_bars`.

* **`ewma_vol`**, carried in the fair table as `sigma_realised`.
* **`f`, the share of daily variance realised inside the session.** Polymarket
  watches Pyth candles between 09:30 and 16:00 ET only, so a barrier breached
  overnight and recovered before the open never counts (MODELS 3.3). `f`
  decides how much of the path is monitored and how much arrives as an
  unobservable gap.

Daily highs and lows from yfinance are the regular session, which is exactly
the window Pyth candles cover, so no adjustment is needed to make the two
agree.
"""
from __future__ import annotations

import math

import numpy as np

TRADING_DAYS = 252.0


def ewma_vol(closes, halflife: int = 20) -> np.ndarray:
    """Annualised EWMA volatility of daily log returns, MODELS 3.5.

    Returned aligned to `closes`: element i uses returns up to and including
    day i, so a value read at day i is known at that day's close and never
    peeks. The first element is NaN (no return yet).
    """
    c = np.asarray(closes, dtype=float)
    out = np.full(c.shape, np.nan)
    if c.size < 2:
        return out
    rets = np.diff(np.log(c))
    lam = 0.5 ** (1.0 / halflife)
    var = float(np.nanvar(rets[: min(len(rets), halflife)]))
    if not np.isfinite(var) or var <= 0:
        var = float(np.nanmean(rets ** 2))
    for i, r in enumerate(rets):
        if np.isfinite(r):
            var = lam * var + (1 - lam) * r * r
        out[i + 1] = math.sqrt(var * TRADING_DAYS)
    return out


# ------------------------------------------------------- intraday share (f)


def intraday_variance_share(opens, closes, min_sessions: int = 60) -> float:
    """`f`: the fraction of daily variance that arrives inside the session.

    Daily variance splits cleanly in two because the overnight gap and the
    intraday move are (near enough) independent:

        ln(C_i / C_{i-1}) = ln(O_i / C_{i-1}) + ln(C_i / O_i)
                             \\_____ gap _____/   \\___ session ___/

    `f = var(session) / (var(gap) + var(session))`. MODELS 3.3 expects 0.5-0.7
    for single names and higher for SPY, which is the check to run on the
    numbers this returns rather than on this docstring.

    Returns NaN on too little data; the caller then falls back to a default
    rather than pricing off a number estimated from a handful of sessions.
    """
    o = np.asarray(opens, dtype=float)
    c = np.asarray(closes, dtype=float)
    if len(o) != len(c) or len(c) < min_sessions + 1:
        return float("nan")
    gap = np.log(o[1:] / c[:-1])
    session = np.log(c[1:] / o[1:])
    keep = np.isfinite(gap) & np.isfinite(session)
    if keep.sum() < min_sessions:
        return float("nan")
    var_gap = float(np.var(gap[keep]))
    var_session = float(np.var(session[keep]))
    total = var_gap + var_session
    if total <= 0:
        return float("nan")
    return var_session / total


def estimate_intraday_share(ticker: str, default: float = 0.6,
                            as_of=None) -> float:
    """`f` for one ticker from its stored daily bars, with a documented default.

    Parquet is read through `model/data.py`, which is the only module allowed
    to touch it.

    `as_of` is not optional in practice: `f` enters every option fair value, so
    estimating it from the whole stored sample lets a cycle in week one know
    how week eight traded. It stays defaulted only so that a caller with no
    clock - a test, or a one-off look at a ticker - does not have to invent one.
    """
    from data import daily_bars, daily_bars_asof

    bars = daily_bars(ticker) if as_of is None else daily_bars_asof(ticker, as_of)
    if bars is None or bars.empty:
        return default
    f = intraday_variance_share(bars["open"].values, bars["close"].values)
    return default if not np.isfinite(f) else float(np.clip(f, 0.05, 0.99))
