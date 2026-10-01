"""Guard results must be unpacked, and a snapshot must respect its deadline.

`should_run_kalshi()` returns (bool, reason). `if should_run_kalshi():` tests the
truthiness of a 2-tuple, which is always True - so Kalshi ran on every tick for
as long as that call site existed, silently ignoring interval_minutes. It cost
~140 s and ~3x the rows on three ticks in four, and nothing failed loudly.
"""
import time

import pytest

import jobs
import kalshi
import poly_books
import util


@pytest.fixture
def stub(monkeypatch):
    """Neutralise every network phase; record which ones ran."""
    ran = {"books": 0, "kalshi": 0, "options": 0}
    monkeypatch.setattr(jobs, "_markets_for_books", lambda run: [])
    import poly_discover
    import poly_events
    monkeypatch.setattr(poly_discover, "discover", lambda *a, **k: ([], True))
    monkeypatch.setattr(poly_discover, "tracked_markets", lambda *a, **k: [])
    monkeypatch.setattr(poly_events, "refresh_events", lambda *a, **k: {})
    monkeypatch.setattr(jobs, "write_meta", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "read_meta", lambda *a, **k: None)
    monkeypatch.setattr(poly_books, "collect_books",
                        lambda *a, **k: (ran.__setitem__("books", ran["books"] + 1), ({}, {}))[1])
    monkeypatch.setattr(kalshi, "collect",
                        lambda *a, **k: (ran.__setitem__("kalshi", ran["kalshi"] + 1), {})[1])
    import options
    monkeypatch.setattr(options, "collect_options",
                        lambda *a, **k: (ran.__setitem__("options", ran["options"] + 1), {})[1])
    monkeypatch.setattr(jobs, "is_regular_hours", lambda *a, **k: True)
    return ran


def test_kalshi_guard_is_unpacked_not_truthiness_tested(stub, monkeypatch):
    monkeypatch.setattr(jobs, "should_run_kalshi", lambda *a: (False, "not a boundary"))
    run = jobs.Run("snapshot")
    jobs.job_snapshot(run)
    assert stub["kalshi"] == 0, "a (False, reason) tuple must not run Kalshi"
    assert stub["books"] == 1 and stub["options"] == 1


def test_kalshi_runs_when_the_guard_says_so(stub, monkeypatch):
    monkeypatch.setattr(jobs, "should_run_kalshi", lambda *a: (True, "every tick"))
    jobs.job_snapshot(jobs.Run("snapshot"))
    assert stub["kalshi"] == 1


def test_deadline_abandons_later_phases(stub, monkeypatch):
    """A blown budget must cost the rest of THIS run, not the next tick."""
    monkeypatch.setattr(jobs, "should_run_kalshi", lambda *a: (True, "every tick"))
    run = jobs.Run("snapshot", deadline_seconds=0.001)
    time.sleep(0.01)
    jobs.job_snapshot(run)
    assert run.status == "truncated"
    assert stub["books"] == 1, "work already done is still written"
    assert stub["kalshi"] == 0 and stub["options"] == 0


def test_no_deadline_means_no_truncation(stub, monkeypatch):
    monkeypatch.setattr(jobs, "should_run_kalshi", lambda *a: (True, "every tick"))
    run = jobs.Run("snapshot")
    assert run.out_of_time() is False
    jobs.job_snapshot(run)
    assert run.status == "ok"


def test_http_stops_retrying_past_the_deadline():
    """The retry ladder is what turned a blip into a 26-minute run."""
    http = util.Http(deadline=util.steady_seconds() - 1)
    assert http.past_deadline() is True
    got = http.get("https://clob.polymarket.com/book", venue="polymarket", identifier="x")
    assert got is None
    assert http.failures and "deadline" in http.failures[0]["error"]
