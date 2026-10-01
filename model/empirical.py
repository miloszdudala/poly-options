"""Empirical touch frequency and the intraday variance share (MODELS 3.5, 3.3).

Two things the option chain cannot tell you, both read straight off
`underlying_bars`:

* **How often this ticker's regular-hours high or low actually reached a given
  distance**, tabulated on a grid of (k, h) - k standard moves, h sessions.
  No Black-Scholes, no implied vol, no choice of which smile point to trust.
  P1 measured that this beats the market price on Brier (0.1083 against
  0.1229 over 1.6M hourly points, Feb-Aug 2026), which is why it is a
  first-class fair value in the engine and not a sanity check.
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

# MODELS 3.5: k in 0.25 steps to 4, h in 1..23. k = 0 is carried so that
# interpolation near the money is anchored on a measured value rather than an
# extrapolation.
K_GRID = np.round(np.arange(0.0, 4.0001, 0.25), 4)
H_GRID = np.arange(1, 24)


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


# -------------------------------------------------------- the touch table


class TouchTable:
    """Historical touch frequency on a (k, h) grid for one ticker, MODELS 3.5.

    `up[h, k]` is the fraction of h-session windows in which the regular-hours
    HIGH reached `close * exp(+k * sigma_daily * sqrt(h))`, with `sigma_daily`
    the EWMA volatility known at the window's first close. `down[h, k]` is the
    same for the LOW and `exp(-k * sigma_daily * sqrt(h))`.
    """

    def __init__(self, up: np.ndarray, down: np.ndarray, counts: np.ndarray,
                 ks: np.ndarray = K_GRID, hs: np.ndarray = H_GRID):
        self.up, self.down, self.counts, self.ks, self.hs = up, down, counts, ks, hs

    def lookup(self, k, h, direction="up"):
        """Bilinear interpolation in (k, h); both axes clamped to the grid."""
        grid = self.up if (direction == "up" if isinstance(direction, str) else True) else self.down
        return _bilinear(grid, self.hs, self.ks, h, k)

    def lookup_many(self, k, h, is_up) -> np.ndarray:
        k = np.asarray(k, dtype=float)
        h = np.asarray(h, dtype=float)
        is_up = np.asarray(is_up, dtype=bool)
        out = np.full(k.shape, np.nan)
        for grid, mask in ((self.up, is_up), (self.down, ~is_up)):
            if mask.any():
                out[mask] = _bilinear(grid, self.hs, self.ks, h[mask], k[mask])
        return out


def _bilinear(grid: np.ndarray, hs: np.ndarray, ks: np.ndarray, h, k) -> np.ndarray:
    """Interpolate `grid[h, k]`, clamping both coordinates into the grid."""
    h = np.clip(np.asarray(h, dtype=float), hs[0], hs[-1])
    k = np.clip(np.asarray(k, dtype=float), ks[0], ks[-1])
    hi = np.clip(np.searchsorted(hs, h, side="right") - 1, 0, len(hs) - 2)
    ki = np.clip(np.searchsorted(ks, k, side="right") - 1, 0, len(ks) - 2)
    th = (h - hs[hi]) / (hs[hi + 1] - hs[hi])
    tk = (k - ks[ki]) / (ks[ki + 1] - ks[ki])
    g00, g01 = grid[hi, ki], grid[hi, ki + 1]
    g10, g11 = grid[hi + 1, ki], grid[hi + 1, ki + 1]
    return (g00 * (1 - th) * (1 - tk) + g01 * (1 - th) * tk
            + g10 * th * (1 - tk) + g11 * th * tk)


def touch_table(high, low, close, sigma=None, ks: np.ndarray = K_GRID,
                hs: np.ndarray = H_GRID) -> TouchTable:
    """Build a TouchTable from one ticker's daily bars.

    `sigma` is a **daily** volatility, because `h` is counted in sessions:
    `k` is therefore the barrier's distance in log terms divided by
    `sigma_daily * sqrt(h)`, and a caller looking the table up must use the
    same units. It defaults to `ewma_vol(close) / sqrt(252)`, that function
    being annualised by convention (Black-Scholes wants it that way). Getting
    this wrong is silent and total: feeding the annualised number in makes
    every threshold sqrt(252) = 15.9 standard deviations too far away, so the
    whole table collapses to zero past k = 0.25 and every fair value it
    produces reads 0.000.

    Window i starts at close[i] and looks at sessions i+1 .. i+h, so nothing
    inside a window is used to set its own threshold.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    sig = (ewma_vol(close) / math.sqrt(TRADING_DAYS) if sigma is None
           else np.asarray(sigma, dtype=float))

    up = np.zeros((len(hs), len(ks)))
    down = np.zeros((len(hs), len(ks)))
    counts = np.zeros(len(hs), dtype=int)
    n = len(close)
    for hi, h in enumerate(hs):
        last = n - int(h)
        if last <= 0:
            continue
        starts = np.arange(last)
        fwd_max = np.array([high[i + 1:i + 1 + int(h)].max() for i in starts])
        fwd_min = np.array([low[i + 1:i + 1 + int(h)].min() for i in starts])
        s0, v0 = close[starts], sig[starts]
        keep = np.isfinite(s0) & np.isfinite(v0) & (v0 > 0) & np.isfinite(fwd_max)
        if not keep.any():
            continue
        move = v0[keep, None] * math.sqrt(float(h)) * ks[None, :]
        up[hi] = (fwd_max[keep, None] >= s0[keep, None] * np.exp(move)).mean(axis=0)
        down[hi] = (fwd_min[keep, None] <= s0[keep, None] * np.exp(-move)).mean(axis=0)
        counts[hi] = int(keep.sum())
    return TouchTable(up, down, counts, ks, hs)
