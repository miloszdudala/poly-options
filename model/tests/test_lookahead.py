"""P6: nothing read at cycle `t` may be timestamped after `t`.

The trap this file exists for is that a daily bar for session D is stamped
`D 04:00Z` - midnight Eastern, *before* the session it summarises. So during
D's own session `bar_time < now` is true and the bar's close, set at 16:00 ET,
is visible at a 14:00Z cycle. Everything downstream of a daily bar inherits
that: the EWMA volatility, `f`, and the betas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import data
import empirical
import factors

MIDSESSION = pd.Timestamp("2026-09-16T14:00:00Z")


def synthetic(dates, closes, opens=None, highs=None, lows=None, symbol="X"):
    """Daily bars stamped the way the store stamps them: Eastern midnight.

    Which is 04:00Z in summer and 05:00Z in winter - 830 and 425 of SPY's
    1,255 stored bars respectively. Hard-coding 04:00Z would make a January
    fixture land at 23:00 ET on the previous day and quietly shift every
    session by one.
    """
    times = (pd.to_datetime(dates).tz_localize("America/New_York")
             .tz_convert("UTC"))
    return pd.DataFrame({
        "symbol": symbol, "bar_time": times,
        "bar_time_utc": [t.isoformat() for t in times],
        "open": opens if opens is not None else closes,
        "high": highs if highs is not None else closes,
        "low": lows if lows is not None else closes,
        "close": closes, "volume": 1.0,
    })


def test_the_store_stamps_a_daily_bar_at_eastern_midnight():
    """Load-bearing for `daily_bars_asof`, which normalises in Eastern to find
    the session a bar belongs to. If the store ever switched to 00:00Z, every
    winter bar would move a day and the as-of cut would be off by one."""
    stamps = data.daily_bars("SPY")["bar_time"].dt.tz_convert("America/New_York")
    assert set(stamps.dt.hour) == {0}
    assert set(stamps.dt.minute) == {0}


# ------------------------------------------- the bar dated today is the future


def test_a_bar_stamped_today_is_not_visible_during_todays_session(monkeypatch):
    """`2026-09-16T04:00Z` carries the 16:00 ET close of 2026-09-16. A cycle at
    14:00Z that same day must not see it."""
    bars = synthetic(["2026-09-14", "2026-09-15", "2026-09-16"], [100.0, 101.0, 90.0])
    monkeypatch.setattr(data, "daily_bars", lambda symbol: bars)
    visible = data.daily_bars_asof("X", MIDSESSION)
    assert list(visible["close"]) == [100.0, 101.0]


def test_yesterdays_bar_is_visible_because_its_session_has_closed(monkeypatch):
    bars = synthetic(["2026-09-14", "2026-09-15"], [100.0, 101.0])
    monkeypatch.setattr(data, "daily_bars", lambda symbol: bars)
    assert len(data.daily_bars_asof("X", MIDSESSION)) == 2


def test_todays_bar_becomes_visible_once_todays_session_has_closed(monkeypatch):
    """After 16:00 ET the close is a fact, not a forecast. Nothing in the
    replay asks for this - cycles stop at 20:00Z - but a rule that only works
    inside the session is a rule waiting to be wrong."""
    bars = synthetic(["2026-09-15", "2026-09-16"], [101.0, 90.0])
    monkeypatch.setattr(data, "daily_bars", lambda symbol: bars)
    after = pd.Timestamp("2026-09-16T21:00:00Z")
    assert list(data.daily_bars_asof("X", after)["close"]) == [101.0, 90.0]


def test_the_realised_vol_a_cycle_sees_does_not_know_todays_close(monkeypatch):
    """A 10% crash today must not raise the volatility a 14:00Z cycle prices on."""
    quiet = [100.0 + 0.1 * i for i in range(400)]
    dates = [d.date().isoformat() for d in pd.bdate_range("2025-01-02", periods=401)]
    calm = synthetic(dates[:400], quiet)
    crash = synthetic(dates[:401], quiet + [quiet[-1] * 0.90])
    monkeypatch.setattr(data, "daily_bars", lambda symbol: crash)
    cycle = pd.Timestamp(f"{dates[400]}T14:00:00Z")
    seen = data.daily_bars_asof("X", cycle)
    assert len(seen) == 400
    assert empirical.ewma_vol(seen["close"].values)[-1] == pytest.approx(
        empirical.ewma_vol(calm["close"].values)[-1])


def test_the_intraday_variance_share_is_taken_as_of_the_cycle(monkeypatch):
    """`f` enters every option fair value. Estimating it from the whole stored
    sample lets a cycle in week one know how week eight traded."""
    dates = [d.date().isoformat() for d in pd.bdate_range("2025-01-02", periods=300)]
    closes = list(100 + np.cumsum(np.full(300, 0.05)))
    opens = [c - 0.02 for c in closes]
    monkeypatch.setattr(data, "daily_bars", lambda symbol: synthetic(dates, closes, opens=opens))
    cycle = pd.Timestamp(f"{dates[200]}T14:00:00Z")
    trimmed = data.daily_bars_asof("X", cycle)
    assert len(trimmed) == 200
    expected = empirical.intraday_variance_share(trimmed["open"].values,
                                                 trimmed["close"].values)
    got = empirical.estimate_intraday_share("X", 0.6, as_of=cycle)
    assert got == pytest.approx(np.clip(expected, 0.05, 0.99))


def test_a_beta_does_not_know_todays_close(monkeypatch):
    rng = np.random.default_rng(3)
    steps = rng.normal(0, 0.01, 300)
    dates = [d.date().isoformat() for d in pd.bdate_range("2025-01-02", periods=300)]
    spy = synthetic(dates, 100 * np.exp(np.cumsum(steps)), symbol="SPY")
    mine = synthetic(dates, 50 * np.exp(np.cumsum(2 * steps)))
    crashed = mine.copy()
    crashed.loc[crashed.index[-1], "close"] *= 0.5

    factors._beta_cached.cache_clear()
    monkeypatch.setattr(data, "daily_bars",
                        lambda s: spy if s == "SPY" else crashed)
    during = factors.beta("X", pd.Timestamp(f"{dates[-1]}T14:00:00Z"))
    factors._beta_cached.cache_clear()
    monkeypatch.setattr(data, "daily_bars", lambda s: spy if s == "SPY" else mine)
    clean = factors.beta("X", pd.Timestamp(f"{dates[-1]}T14:00:00Z"))
    factors._beta_cached.cache_clear()
    assert during == pytest.approx(clean)


# ----------------------------------------------------- duplicated bar times


def test_the_store_returns_one_daily_bar_per_timestamp():
    """Every one of the 19 equity tickers carries at least one duplicated daily
    bar time: P1's backfill appended hourly bars beside the collector's daily
    ones and `select distinct` keeps both when the closes differ in the last
    decimal. A duplicate is a spurious zero return in the EWMA and a spurious
    same-day 'gap' in `f`, on every ticker at once."""
    for ticker in ("SPY", "NVDA", "PLTR", "EWY", "AAPL", "TSLA"):
        bars = data.daily_bars(ticker)
        assert bars["bar_time"].is_unique, ticker


def test_duplicate_bars_are_removed_before_anything_derives_from_them(monkeypatch):
    bars = synthetic(["2026-09-14", "2026-09-15", "2026-09-15"],
                     [100.0, 101.0, 101.5])
    monkeypatch.setattr(data, "_q", lambda sql: bars.drop(columns=["bar_time"]))
    data.daily_bars.cache_clear()
    out = data.daily_bars("X")
    data.daily_bars.cache_clear()
    assert len(out) == 2
    assert out["close"].iloc[-1] == 101.5      # last write wins


# ------------------------------------------- review 2026-09-18: bars are periods


def five_minute(symbol, stamps, opens, highs, lows, closes):
    times = pd.to_datetime(stamps, utc=True)
    return pd.DataFrame({"symbol": symbol, "bar_time": times,
                         "bar_time_utc": [t.isoformat() for t in times],
                         "open": opens, "high": highs, "low": lows, "close": closes})


def test_spot_is_the_open_of_the_containing_bar_never_its_close(monkeypatch):
    """A 15:50Z bar closes at 15:54:59; a 15:51Z book cannot know that print.
    Its open is the first print of the bar, at or before the book."""
    bars = five_minute("X", ["2026-09-16T15:45:00Z", "2026-09-16T15:50:00Z"],
                       opens=[100.0, 101.0], highs=[101.5, 108.0], lows=[99.0, 100.0],
                       closes=[101.0, 108.0])
    monkeypatch.setattr(data, "bars_5m", lambda: bars)
    spot = data.spot_at(pd.Series([pd.Timestamp("2026-09-16T15:51:29Z")]), pd.Series(["X"]))
    assert spot.iloc[0] == 101.0


def test_a_barrier_printed_earlier_in_the_window_is_a_touch(monkeypatch):
    """SPY, 2026-09-16: low 749.60 at 19:25Z, spot 752.42 at 19:36Z. The
    hit_down 750 was already a YES when the engine priced its NO at 0.79."""
    bars = five_minute("SPY", ["2026-09-16T19:25:00Z", "2026-09-16T19:30:00Z"],
                       opens=[751.0, 750.5], highs=[751.5, 752.5], lows=[749.6, 750.4],
                       closes=[750.5, 752.4])
    monkeypatch.setattr(data, "bars_5m", lambda: bars)
    data.session_bars_5m.cache_clear()
    assert data.touched_between("SPY", 750.0, False, "2026-09-14T04:00:00Z",
                                "2026-09-16T19:36:37Z") is True
    assert data.touched_between("SPY", 749.0, False, "2026-09-14T04:00:00Z",
                                "2026-09-16T19:36:37Z") is False
    assert data.touched_between("SPY", 752.5, True, "2026-09-14T04:00:00Z",
                                "2026-09-16T19:36:37Z") is True


def test_the_touch_check_ignores_the_bar_still_forming(monkeypatch):
    bars = five_minute("X", ["2026-09-16T19:30:00Z", "2026-09-16T19:35:00Z"],
                       opens=[100.0, 100.0], highs=[100.5, 120.0], lows=[99.5, 99.0],
                       closes=[100.0, 120.0])
    monkeypatch.setattr(data, "bars_5m", lambda: bars)
    data.session_bars_5m.cache_clear()
    # 19:35 bar finishes at 19:40: unknown at 19:37, known at 19:40.
    assert data.touched_between("X", 110.0, True, "2026-09-16T00:00:00Z",
                                "2026-09-16T19:37:00Z") is False
    assert data.touched_between("X", 110.0, True, "2026-09-16T00:00:00Z",
                                "2026-09-16T19:40:00Z") is True


def test_the_touch_check_ignores_extended_hours_and_the_time_before_the_period(monkeypatch):
    """The rules text: regular hours only, from the start of the period."""
    bars = five_minute("X", ["2026-09-15T19:55:00Z",   # Tuesday, before the window
                             "2026-09-16T12:00:00Z",   # 08:00 ET, pre-market
                             "2026-09-16T14:00:00Z"],  # regular hours
                       opens=[100.0] * 3, highs=[130.0, 125.0, 101.0],
                       lows=[99.0] * 3, closes=[100.0] * 3)
    monkeypatch.setattr(data, "bars_5m", lambda: bars)
    data.session_bars_5m.cache_clear()
    assert data.touched_between("X", 120.0, True, "2026-09-16T04:00:00Z",
                                "2026-09-16T15:00:00Z") is False
    assert data.touched_between("Y", 120.0, True, "2026-09-16T04:00:00Z",
                                "2026-09-16T15:00:00Z") is None


def test_period_start_is_monday_or_the_first_at_eastern_midnight():
    from sessions import period_start

    friday_close = pd.Timestamp("2026-09-18T20:00:00Z")
    assert period_start(friday_close, "weekly") == pd.Timestamp("2026-09-14T04:00:00Z")
    month_end = pd.Timestamp("2026-09-30T20:00:00Z")
    assert period_start(month_end, "monthly") == pd.Timestamp("2026-09-01T04:00:00Z")
    # Winter: Eastern midnight is 05:00Z, and the tz database knows that.
    assert period_start(pd.Timestamp("2026-12-31T21:00:00Z"), "monthly") == \
        pd.Timestamp("2026-12-01T05:00:00Z")
    assert pd.isna(period_start(friday_close, "daily"))


def test_a_touch_counts_only_after_the_market_was_created():
    """MU hit_down 930 (4641541), created Thursday 2026-09-17 16:38Z, resolved
    NO although MU printed 902.60 on Monday: its rules say "after market
    creation". 75 of 330 touch_pending markets were this."""
    from sessions import watch_start

    friday_close = pd.Timestamp("2026-09-18T20:00:00Z")
    created = pd.Timestamp("2026-09-17T16:38:47Z")
    assert watch_start(friday_close, "weekly", created, True) == created
    # Older rules watch the whole period.
    assert watch_start(friday_close, "weekly", created, False) == \
        pd.Timestamp("2026-09-14T04:00:00Z")
    # Created before the period (a monthly listed in August): the period wins.
    assert watch_start(pd.Timestamp("2026-09-30T20:00:00Z"), "monthly",
                       pd.Timestamp("2026-08-20T00:00:00Z"), True) == \
        pd.Timestamp("2026-09-01T04:00:00Z")
    assert watch_start(friday_close, "weekly", None, True) == pd.Timestamp("2026-09-14T04:00:00Z")


def test_the_touch_check_falls_back_to_hourly_and_daily_bars(monkeypatch):
    """Before 2026-06-22 there are no 5-minute bars; the hourly and daily
    series carry the same regular-session highs and lows."""
    empty = five_minute("X", [], [], [], [], [])
    monkeypatch.setattr(data, "bars_5m", lambda: empty)
    hourly = five_minute("X", ["2026-03-03T14:30:00Z"], opens=[100.0], highs=[112.0],
                         lows=[99.0], closes=[101.0])
    monkeypatch.setattr(data, "bars_1h", lambda: hourly)
    daily = synthetic(["2026-03-02"], closes=[100.0], highs=[105.0], lows=[90.0])
    monkeypatch.setattr(data, "daily_bars", lambda symbol: daily)
    data.session_bars_5m.cache_clear(); data.session_bars_1h.cache_clear()
    start = "2026-03-02T05:00:00Z"
    # The hourly bar is finished at 15:30Z, not at 15:00Z.
    assert data.touched_between("X", 110.0, True, start, "2026-03-03T15:00:00Z") is False
    assert data.touched_between("X", 110.0, True, start, "2026-03-03T15:30:00Z") is True
    # The daily bar of 03-02 is known from 16:00 ET that day.
    assert not data.touched_between("X", 92.0, False, start, "2026-03-02T20:00:00Z")
    assert data.touched_between("X", 92.0, False, start, "2026-03-02T21:00:00Z") is True


def test_earnings_intervals_are_half_open_and_as_of_falls_back_to_the_first_fetch(monkeypatch):
    """MU reports 2026-09-30 at the bell; its monthly deadline is that close.
    The report is not inside the barrier's window, it is in the gap."""
    table = pd.DataFrame({
        "symbol": ["MU", "MU"],
        "when": pd.to_datetime(["2026-09-30T20:00:00Z", "2026-12-16T21:00:00Z"], utc=True),
        "first_seen": pd.to_datetime(["2026-09-16T21:44:00Z", "2026-10-05T21:44:00Z"], utc=True),
    })
    monkeypatch.setattr(data, "upcoming_earnings", lambda: table)
    deadline = pd.Timestamp("2026-09-30T20:00:00Z")
    after = pd.Timestamp("2026-10-02T20:00:00Z")
    assert data.earnings_between("MU", "2026-09-16T14:00:00Z", deadline) is False
    assert data.earnings_between("MU", deadline, after) is True
    # A date first fetched in October is invisible to a September cycle, but
    # a cycle before the first fetch of all still sees the earliest fetch.
    assert data.earnings_between("MU", "2026-12-01", "2026-12-31",
                                 as_of="2026-09-20T14:00:00Z") is False
    assert data.earnings_between("MU", deadline, after, as_of="2026-09-16T14:00:00Z") is True


def test_a_same_day_settlement_is_seen_by_a_later_cycle(tmp_path, monkeypatch):
    """Stored stamps are ISO ('T'), `str(pd.Timestamp)` has a space: compared as
    text, a 13:48Z settlement sorted after a 19:45Z cutoff on the same date and
    the last day of every run never settled (review 2026-10-09)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = tmp_path / "resolutions"
    table.mkdir()
    rows = {"market_id": ["early", "late"], "result": ["Yes", "Yes"],
            "settled_at_utc": ["2026-10-08T13:48:42+00:00", "2026-10-08T20:44:56+00:00"],
            "fetched_at_utc": ["2026-10-08T21:31:32+00:00"] * 2,
            "ticker": ["META", "DXY"], "contract_type": ["hit_down", "hit_up"],
            "barrier": [720.0, 102.5], "outcome_prices": ["[1, 0]", "[1, 0]"]}
    pq.write_table(pa.table(rows), table / "part-0.parquet")
    monkeypatch.setattr(data, "PARQUET_DIR", tmp_path)
    for cutoff in (pd.Timestamp("2026-10-08T19:45:01Z"), "2026-10-08 19:45:01+00:00",
                   pd.Timestamp("2026-10-08 15:45:01", tz="America/New_York")):
        seen = set(data.resolutions_upto(cutoff)["market_id"])
        assert seen == {"early"}, cutoff


def test_daily_bars_keep_the_newest_non_empty_version(tmp_path, monkeypatch):
    """The dedupe used to keep whichever duplicate DuckDB returned last, which
    changed run to run and moved every beta (2026-10-09)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = tmp_path / "underlying_bars"
    table.mkdir()
    stamp = "2026-09-22T04:00:00+00:00"
    rows = {"symbol": ["META"] * 3, "interval": ["1d"] * 3, "bar_time_utc": [stamp] * 3,
            "open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3,
            "close": [736.594971, None, 736.599976], "volume": [1.0] * 3,
            "fetched_at_utc": ["2026-09-22T21:41:26+00:00", "2026-09-25T01:00:00+00:00",
                               "2026-09-24T20:40:16+00:00"]}
    for i in range(3):        # one file per row, so file order cannot decide it
        pq.write_table(pa.table({k: [v[i]] for k, v in rows.items()}), table / f"part-{i}.parquet")
    monkeypatch.setattr(data, "PARQUET_DIR", tmp_path)
    bars = data.daily_bars.__wrapped__("META") if hasattr(data.daily_bars, "__wrapped__") \
        else data.daily_bars("META")
    assert bars["close"].tolist() == [736.599976]
