"""What a closed laptop does to the collector, and the guards that survive it.

The 2026-09-17 session produced 4 snapshots out of ~26 scheduled and zero
Kalshi books. The machine was not off: macOS held the clamshell in Deep Idle
and dark-woke for 2-45 seconds every 15-35 minutes. launchd fired the job on
those wakes, so every run was suspended mid-flight and resumed on the next
one. Everything here is a regression test for something that broke because of
that, measured against the run rows the session actually wrote.
"""
import datetime as dt
import time
from zoneinfo import ZoneInfo

import pytest

import storage
import util
from util import UTC, should_run_kalshi, steady_seconds

ET = ZoneInfo("America/New_York")


# ------------------------------------------------------- the deadline's clock


def test_steady_clock_does_not_freeze_during_system_sleep():
    """time.monotonic() stops across suspend; the deadline clock must not.

    Measured on the collecting Mac 2026-09-18: 31.3 of 61.4 hours since boot
    were missing from time.monotonic(). This asserts the property that matters
    - steady_seconds() is at least monotonic, and on any platform with a
    suspend-aware clock it is strictly ahead of it.
    """
    assert steady_seconds() >= time.monotonic()


def test_steady_clock_is_monotonic():
    first = steady_seconds()
    time.sleep(0.01)
    assert steady_seconds() > first


def test_run_deadline_uses_the_steady_clock(monkeypatch):
    """A run must time out on wall-clock seconds, not awake seconds.

    The bug: a 600 s deadline built on time.monotonic() let the 15:45 ET tick
    of 2026-09-17 run for 57 minutes and write books at 16:41 ET, after the
    close, into a cycle labelled 15:45.
    """
    import jobs

    fake = {"now": 1000.0}
    monkeypatch.setattr(jobs, "steady_seconds", lambda: fake["now"])
    run = jobs.Run("snapshot", deadline_seconds=600)
    assert run.out_of_time() is False
    fake["now"] += 599          # still inside the budget
    assert run.out_of_time() is False
    fake["now"] += 2            # wall clock passed it, even if asleep for it
    assert run.out_of_time() is True


# ------------------------------------------------------ the Kalshi cadence


def _at(hh, mm):
    return dt.datetime(2026, 9, 17, hh, mm, tzinfo=ET).astimezone(UTC)


def test_kalshi_fires_on_a_tick_that_missed_the_grid():
    """The four ET minutes that lost a whole session of Kalshi books.

    Ticks landed at minutes 18, 31, 38 and 41 because launchd fires on the
    next dark wake, not on the quarter hour. `minute % 60 < 15` rejected every
    one of them. An hour of elapsed time must be enough on its own.
    """
    for hour, minute in ((11, 18), (12, 31), (13, 38), (15, 41)):
        when = _at(hour, minute)
        last = when - dt.timedelta(minutes=61)
        ran, why = should_run_kalshi(when, last)
        assert ran is True, f"{hour}:{minute} rejected: {why}"


def test_kalshi_does_not_run_twice_inside_its_interval():
    when = _at(11, 18)
    ran, why = should_run_kalshi(when, when - dt.timedelta(minutes=20))
    assert ran is False and "20 of 60" in why


def test_kalshi_tolerates_a_tick_arriving_just_early():
    """A tick at 53 minutes must collect, not defer an hour to the next one."""
    when = _at(11, 18)
    ran, _ = should_run_kalshi(when, when - dt.timedelta(minutes=53))
    assert ran is True


def test_kalshi_runs_when_it_has_never_run():
    assert should_run_kalshi(_at(11, 18), None)[0] is True


def test_kalshi_survives_a_last_run_stamp_in_the_future():
    """A clock step must not wedge the guard shut until the stamp passes."""
    when = _at(11, 18)
    ran, why = should_run_kalshi(when, when + dt.timedelta(hours=3))
    assert ran is True and "future" in why


def test_kalshi_every_tick_when_the_interval_is_the_tick(monkeypatch):
    import config

    cfg = config.markets_cfg()
    patched = {**cfg, "kalshi": {**cfg["kalshi"], "interval_minutes": 15}}
    monkeypatch.setattr(util, "markets_cfg", lambda: patched)
    assert should_run_kalshi(_at(11, 18), _at(11, 17))[0] is True


# --------------------------------------------------------- partition by cycle


def test_partition_follows_the_cycle_not_the_write_clock(tmp_path, monkeypatch):
    """One cycle_id must never straddle two date= partitions.

    The 2026-09-17 daily job started 21:32Z and was still writing at 01:44Z,
    so its rows split across date=2026-09-17 and date=2026-09-18 under a
    single cycle_id. Reading one day's partition then drops rows in silence.
    """
    monkeypatch.setattr(storage, "PARQUET_DIR", tmp_path)
    monkeypatch.setattr(storage, "now_utc",
                        lambda: dt.datetime(2026, 9, 18, 1, 44, tzinfo=UTC))
    rows = [{"cycle_id": "20260917T213200Z-abc123", "fetched_at_utc": "x"}]
    path = storage.write_table("poly_price_history", rows)
    assert path.parent.name == "date=2026-09-17"


def test_partition_falls_back_to_the_clock_without_a_cycle_id(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PARQUET_DIR", tmp_path)
    monkeypatch.setattr(storage, "now_utc",
                        lambda: dt.datetime(2026, 9, 18, 1, 44, tzinfo=UTC))
    path = storage.write_table("poly_price_history", [{"fetched_at_utc": "x"}])
    assert path.parent.name == "date=2026-09-18"


def test_explicit_partition_date_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PARQUET_DIR", tmp_path)
    rows = [{"cycle_id": "20260917T213200Z-abc123", "fetched_at_utc": "x"}]
    path = storage.write_table("poly_price_history", rows,
                               partition_date="2026-01-01")
    assert path.parent.name == "date=2026-01-01"


# ------------------------------------------------------- the book fetch stamp


def test_book_rows_carry_the_time_their_batch_was_read(monkeypatch):
    """fetched_at_utc must show the smear, not hide it behind one stamp.

    A cycle whose books took 126 minutes recorded every row at the cycle's
    start instant, so nothing downstream could tell a fresh book from a
    two-hour-old one.
    """
    import poly_books

    stamps = iter(["2026-09-17T17:38:25+00:00", "2026-09-17T18:40:00+00:00"])
    monkeypatch.setattr(poly_books, "utc_iso", lambda *a: next(stamps))

    markets = [{"market_id": str(i), "token_id_0": f"t{i}a", "token_id_1": f"t{i}b",
                "outcome_0": "Yes", "outcome_1": "No"} for i in range(2)]

    class FakeHttp:
        failures: list = []

        def post(self, url, **kw):
            return [{"asset_id": t, "bids": [{"price": "0.4", "size": "10"}],
                     "asks": [], "timestamp": "1789000000000"}
                    for t in kw["json"] and [d["token_id"] for d in kw["json"]]]

    written: list = []
    monkeypatch.setattr(poly_books, "write_table",
                        lambda table, rows, **k: written.extend(rows))
    monkeypatch.setattr(poly_books, "markets_cfg", lambda: {
        "polymarket": {"clob_base": "https://x", "books_batch_size": 2}})
    monkeypatch.setattr(poly_books, "raw_enabled", lambda kind: False)

    poly_books.collect_books(FakeHttp(), markets, "20260917T173825Z-0c59dd")

    seen = {r["fetched_at_utc"] for r in written}
    assert len(seen) == 2, f"batches must be stamped separately, got {seen}"
