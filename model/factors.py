"""Betas to SPY, computed only from bars the cycle could already have seen.

MODELS 5.2 sizes model B on beta-dollar-delta, so a beta is an input to a
trading decision and is therefore subject to the same no-lookahead rule as
everything else in the replay: the regression window ends at the last daily
close *strictly before* the cycle's own timestamp. Using the full sample -
the obvious thing, and what every `yfinance` example does - would let a
2026-09-16 position be sized on how the stock traded in October.

One regression per (ticker, as-of date), cached, because a replay asks for the
same 24 betas 21 times a day.
"""
from __future__ import annotations

import functools

import numpy as np
import pandas as pd

import data

MARKET = "SPY"
BAR_MINUTES = 5       # the interval of the intraday bars the stop reads
WINDOW = 250          # MODELS 5.2: "250-day regression on daily bars"
MIN_SESSIONS = 60     # below this the slope is noise; fall back to 1.0


def _utc(when) -> pd.Timestamp:
    """Every bar time in the store is tz-aware UTC; a naive argument is not an
    error, it is a caller writing a date. Localise rather than refuse."""
    stamp = pd.Timestamp(when)
    return stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")


def _returns(symbol: str, before: pd.Timestamp) -> pd.Series:
    """Close-to-close log returns indexed by bar time, strictly before `before`.

    `daily_bars_asof` does both jobs the store needs: it drops the duplicated
    session stamps (P1's backfill appended hourly bars beside the daily ones,
    and a duplicated index makes the join against SPY fail outright), and it
    refuses the bar for a session that has not closed yet. A daily bar is
    stamped at midnight Eastern, so `bar_time < before` would have handed a
    14:00Z cycle its own day's 16:00 ET close.
    """
    past = data.daily_bars_asof(symbol, before)
    if len(past) < 2:
        return pd.Series(dtype=float)
    closes = past["close"].astype(float).values
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.diff(np.log(closes))
    return pd.Series(rets, index=past["bar_time"].values[1:])


@functools.lru_cache(maxsize=4096)
def _beta_cached(ticker: str, as_of_iso: str) -> tuple[float, int]:
    before = _utc(as_of_iso)
    mine = _returns(ticker, before)
    market = _returns(MARKET, before)
    if mine.empty or market.empty:
        return 1.0, 0
    joined = pd.DataFrame({"x": market, "y": mine}).dropna().tail(WINDOW)
    joined = joined[np.isfinite(joined["x"]) & np.isfinite(joined["y"])]
    n = len(joined)
    if n < MIN_SESSIONS:
        return 1.0, n
    x = joined["x"].values
    y = joined["y"].values
    var = float(np.var(x))
    if not np.isfinite(var) or var <= 0:
        return 1.0, n
    return float(np.cov(x, y, bias=True)[0, 1] / var), n


def beta(ticker: str, as_of) -> float:
    """Slope of the ticker's daily returns on SPY's, over the last `WINDOW`.

    Returns 1.0 for the market itself, for a ticker with too little history,
    and for anything that is not an equity - the commodity and FX ladders have
    no SPY beta worth the name, and pretending one is 0.0 would let model B
    load up on them for free. 1.0 is the conservative error.
    """
    if ticker == MARKET:
        return 1.0
    return _beta_cached(ticker, _utc(as_of).isoformat())[0]


def beta_sessions(ticker: str, as_of) -> int:
    """How many sessions the beta was fitted on. A report that prints a beta
    without this number is hiding the difference between a fit and a default."""
    if ticker == MARKET:
        return WINDOW
    return _beta_cached(ticker, _utc(as_of).isoformat())[1]


def beta_table(tickers, as_of) -> dict[str, float]:
    return {t: beta(t, as_of) for t in sorted(set(tickers))}


def spy_intraday_return(as_of) -> float:
    """SPY's move from today's regular-session open to the last *finished* 5m bar.

    Model C's stop rule (MODELS 5.3) keys on this. Two things a replay must not
    do, and both were done here before P6:

    * read the daily bar for today, which is not final until the close;
    * read the 5-minute bar that is still forming. A bar stamped 13:40Z covers
      13:40:00-13:44:59, so at a 13:40Z cycle its close is five minutes ahead.
      MODELS 3.1 asks for the *containing* bar as the spot and the engine
      honours that; this is a stop, not a spot, and a stop that fires on a move
      which has not finished happening is not a stop. SPY's 5-minute
      open-to-close move on 2026-09-16 ran to 0.32% at its largest.
    """
    now = _utc(as_of)
    bars = data.bars_5m()
    done = bars["bar_time"] + pd.Timedelta(minutes=BAR_MINUTES) <= now
    spy = bars[(bars["symbol"] == MARKET) & done]
    if spy.empty:
        return float("nan")
    same_day = spy[spy["bar_time"].dt.date == now.date()]
    if len(same_day) < 2:
        return float("nan")
    first_open = float(same_day["open"].iloc[0])
    last_close = float(same_day["close"].iloc[-1])
    if not (np.isfinite(first_open) and first_open > 0):
        return float("nan")
    return last_close / first_open - 1.0


def spy_daily_returns(start, end) -> pd.Series:
    """SPY close-to-close returns over a date range, for the PnL regression."""
    bars = data.daily_bars(MARKET)
    window = bars[(bars["bar_time"] >= _utc(start) - pd.Timedelta(days=5))
                  & (bars["bar_time"] <= _utc(end) + pd.Timedelta(days=1))]
    window = window.drop_duplicates(subset="bar_time", keep="last")   # see `data.daily_bars`
    if len(window) < 2:
        return pd.Series(dtype=float)
    closes = window["close"].astype(float).values
    rets = np.diff(np.log(closes))
    return pd.Series(rets, index=[pd.Timestamp(t).date() for t in window["bar_time"].values[1:]])
