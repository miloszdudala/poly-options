"""Betas to SPY, on synthetic bars.

The only interesting property is the one that is easy to lose: a beta used to
size a position at cycle `t` may not have seen a bar from after `t`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import factors


@pytest.fixture(autouse=True)
def clear_cache():
    factors._beta_cached.cache_clear()
    yield
    factors._beta_cached.cache_clear()


def bars(closes, start="2025-01-02", symbol="X", highs=None, lows=None, opens=None):
    times = pd.bdate_range(start, periods=len(closes), tz="UTC")
    return pd.DataFrame({
        "symbol": symbol, "bar_time": times, "bar_time_utc": [t.isoformat() for t in times],
        "open": opens if opens is not None else closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "close": closes, "volume": 1.0,
    })


def wire(monkeypatch, series_by_symbol):
    monkeypatch.setattr(factors.data, "daily_bars",
                        lambda symbol: series_by_symbol.get(symbol, bars([], symbol=symbol)))


def test_a_stock_that_moves_twice_as_far_has_a_beta_of_two(monkeypatch):
    rng = np.random.default_rng(7)
    steps = rng.normal(0, 0.01, 300)
    spy = 100 * np.exp(np.cumsum(steps))
    mine = 50 * np.exp(np.cumsum(2 * steps))
    wire(monkeypatch, {"SPY": bars(spy, symbol="SPY"), "X": bars(mine)})
    assert factors.beta("X", "2027-01-01") == pytest.approx(2.0, abs=1e-9)


def test_a_beta_never_sees_a_bar_from_after_the_cycle(monkeypatch):
    """The whole point. A 40% crash after the as-of date must not move the
    beta that sized a position before it."""
    rng = np.random.default_rng(11)
    steps = rng.normal(0, 0.01, 300)
    spy_path = 100 * np.exp(np.cumsum(steps))
    mine_path = 50 * np.exp(np.cumsum(1.5 * steps))
    spy, mine = bars(spy_path, symbol="SPY"), bars(mine_path)
    as_of = mine["bar_time"].iloc[200]

    wire(monkeypatch, {"SPY": spy, "X": mine})
    before = factors.beta("X", as_of)

    crashed = mine.copy()
    crashed.loc[crashed.index[250:], "close"] *= 0.6
    factors._beta_cached.cache_clear()
    wire(monkeypatch, {"SPY": spy, "X": crashed})
    assert factors.beta("X", as_of) == pytest.approx(before)


def test_the_window_is_the_last_two_hundred_and_fifty_sessions(monkeypatch):
    """A regime older than the window is out of it, not merely down-weighted."""
    rng = np.random.default_rng(3)
    steps = rng.normal(0, 0.01, 600)
    spy_path = 100 * np.exp(np.cumsum(steps))
    slopes = np.where(np.arange(600) < 300, 3.0, 1.0)
    mine_path = 50 * np.exp(np.cumsum(slopes * steps))
    wire(monkeypatch, {"SPY": bars(spy_path, symbol="SPY"), "X": bars(mine_path)})
    assert factors.beta("X", "2028-01-01") == pytest.approx(1.0, abs=1e-9)


def test_too_little_history_falls_back_to_one_not_to_zero(monkeypatch):
    """A beta of 0.0 would tell model B a position is free of market risk.
    1.0 is the error that costs something rather than the one that hides."""
    wire(monkeypatch, {"SPY": bars(np.linspace(100, 110, 30), symbol="SPY"),
                       "X": bars(np.linspace(50, 55, 30))})
    assert factors.beta("X", "2027-01-01") == 1.0
    assert factors.beta_sessions("X", "2027-01-01") < factors.MIN_SESSIONS


def test_a_ticker_with_no_bars_at_all_is_beta_one(monkeypatch):
    wire(monkeypatch, {"SPY": bars(np.linspace(100, 110, 300), symbol="SPY")})
    assert factors.beta("WTI", "2027-01-01") == 1.0


def test_spy_has_a_beta_of_one_to_itself_without_a_regression(monkeypatch):
    wire(monkeypatch, {})
    assert factors.beta("SPY", "2027-01-01") == 1.0
    assert factors.beta_sessions("SPY", "2027-01-01") == factors.WINDOW


def test_duplicate_bar_times_do_not_break_the_join(monkeypatch):
    """P1's backfill appended hourly bars beside the collector's daily ones and
    `select distinct` keeps both when their closes differ in the last decimal.
    A duplicated index makes the join against SPY raise outright.

    The dedupe itself moved into `data.daily_bars` in P6, so that one place
    serves the engine and the betas alike. This goes through the real reader to
    pin that `factors` still inherits it - patching `daily_bars` would patch out
    the very thing being tested.
    """
    rng = np.random.default_rng(5)
    steps = rng.normal(0, 0.01, 300)
    frames = {"SPY": bars(100 * np.exp(np.cumsum(steps)), symbol="SPY"),
              "X": bars(50 * np.exp(np.cumsum(2 * steps)))}
    frames["X"] = pd.concat([frames["X"], frames["X"].tail(50)], ignore_index=True)

    def fake_q(sql):
        symbol = "SPY" if "symbol = 'SPY'" in sql else "X"
        return frames[symbol].drop(columns=["bar_time"]).copy()

    monkeypatch.setattr(factors.data, "_q", fake_q)
    factors.data.daily_bars.cache_clear()
    try:
        assert factors.beta("X", "2027-01-01") == pytest.approx(2.0, abs=1e-9)
    finally:
        factors.data.daily_bars.cache_clear()


def test_a_naive_timestamp_is_read_as_utc_rather_than_refused(monkeypatch):
    wire(monkeypatch, {"SPY": bars(np.linspace(100, 110, 300), symbol="SPY"),
                       "X": bars(np.linspace(50, 60, 300))})
    assert np.isfinite(factors.beta("X", "2026-09-16"))


# ------------------------------------------------------ the intraday move


def five_minute(prices, day="2026-09-16", symbol="SPY"):
    times = pd.date_range(f"{day}T13:30:00Z", periods=len(prices), freq="5min", tz="UTC")
    return pd.DataFrame({"symbol": symbol, "bar_time": times,
                         "bar_time_utc": [t.isoformat() for t in times],
                         "open": prices, "high": prices, "low": prices, "close": prices})


def test_the_intraday_move_is_measured_from_the_session_open(monkeypatch):
    """Model C's stop (MODELS 5.3) keys on this, so it is open-to-now and not
    close-to-now: yesterday's close is not what a longshot seller watches.

    Read at 13:45Z, by which time the 13:40Z bar has finished forming - see
    `test_the_stop_reads_only_bars_that_have_finished_forming`."""
    monkeypatch.setattr(factors.data, "bars_5m",
                        lambda: five_minute([600.0, 595.0, 591.0]))
    assert factors.spy_intraday_return("2026-09-16T13:45:00Z") == pytest.approx(-0.015)


def test_the_intraday_move_ignores_bars_from_later_in_the_day(monkeypatch):
    """The 13:40Z bar's -17% is both unfinished and in the future at 13:40Z."""
    monkeypatch.setattr(factors.data, "bars_5m",
                        lambda: five_minute([600.0, 595.0, 500.0]))
    assert factors.spy_intraday_return("2026-09-16T13:40:00Z") == pytest.approx(
        595.0 / 600.0 - 1.0)


def test_a_day_with_no_bars_gives_nan_not_zero(monkeypatch):
    """Zero would read as "SPY is flat, keep selling longshots"."""
    monkeypatch.setattr(factors.data, "bars_5m", lambda: five_minute([600.0]))
    assert np.isnan(factors.spy_intraday_return("2026-09-17T13:40:00Z"))


def test_the_stop_reads_only_bars_that_have_finished_forming(monkeypatch):
    """P6. A 5-minute bar stamped 13:40Z covers 13:40:00-13:44:59, so its close
    is up to five minutes in the future of a 13:40Z cycle. MODELS 3.1 asks for
    the *containing* bar as the spot and that is honoured in the engine; model
    C's -1.5% stop is not covered by that sentence, and a stop that fires on a
    move which has not finished happening is not a stop.

    SPY's 5-minute open-to-close move on 2026-09-16: median 0.031%, p90 0.153%,
    max 0.323%.
    """
    monkeypatch.setattr(factors.data, "bars_5m",
                        lambda: five_minute([600.0, 598.0, 560.0]))
    # 13:40Z is the stamp of the third bar, which runs to 13:44:59.
    assert factors.spy_intraday_return("2026-09-16T13:40:00Z") == pytest.approx(
        598.0 / 600.0 - 1.0)


def test_a_bar_is_used_once_its_five_minutes_are_up(monkeypatch):
    monkeypatch.setattr(factors.data, "bars_5m",
                        lambda: five_minute([600.0, 598.0, 560.0]))
    assert factors.spy_intraday_return("2026-09-16T13:45:00Z") == pytest.approx(
        560.0 / 600.0 - 1.0)
