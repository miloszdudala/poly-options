"""The opening snapshot must not be spent on discovery, and a slow walk resumes.

MEASURED 2026-09-21 and 2026-09-22: the 13:30 UTC tick ran the intraday walk
first. At the US open Gamma served it at ~5 s a page (vs ~0.4 s at 04:30
local), the walk stopped at page 166 and then 116 of ~190 when the 600 s budget
ran out, and every opening book request failed with "run deadline exceeded".
The partial walk was then stamped as last_discovery, so the rest of the walk
was never retried that session.
"""
import pytest

import jobs
import kalshi
import options
import poly_books
import poly_discover
import poly_events


class Pages:
    """Gamma keyset stand-in: fixed pages, and a page that fails on demand."""

    def __init__(self, pages, fail_at=None):
        self.pages = pages
        self.fail_at = fail_at
        self.cursors = []

    def get(self, url, *, params=None, **kw):
        cursor = (params or {}).get("after_cursor")
        self.cursors.append(cursor)
        i = 0 if cursor is None else int(cursor)
        if i == self.fail_at:
            return None
        nxt = str(i + 1) if i + 1 < len(self.pages) else None
        return {"events": self.pages[i], "next_cursor": nxt}


@pytest.fixture
def meta(monkeypatch):
    store = {}
    written = {}
    monkeypatch.setattr(poly_discover, "read_meta", lambda name: store.get(name))
    monkeypatch.setattr(poly_discover, "write_meta",
                        lambda name, payload: store.__setitem__(name, payload))
    monkeypatch.setattr(poly_discover, "write_table",
                        lambda t, rows: written.setdefault(t, []).extend(rows))
    monkeypatch.setattr(poly_discover, "raw_enabled", lambda *a: False)
    monkeypatch.setattr(poly_discover, "event_matches",
                        lambda e, d: (e["id"].startswith("keep"), ""))
    monkeypatch.setattr(poly_discover, "market_row",
                        lambda e, m, c: {"market_id": m["id"], "contract_type": "close_above",
                                         "event_slug": e["id"]})
    monkeypatch.setattr(poly_events, "remember_events", lambda *a: None)
    store["written"] = written
    return store


def _ev(eid):
    return {"id": eid, "markets": [{"id": f"m-{eid}"}]}


PAGES = [[_ev("keep0"), _ev("drop0")], [_ev("keep1")], [_ev("keep2"), _ev("drop2")]]


def test_an_interrupted_walk_writes_nothing_and_saves_its_cursor(meta):
    rows, complete = poly_discover.discover(Pages(PAGES, fail_at=1), "c1")
    assert (rows, complete) == ([], False)
    assert "poly_markets" not in meta["written"], "a partial walk must not stand in for a full one"
    assert meta["discovery_resume"]["cursor"] == "1"
    assert [e["id"] for e in meta["discovery_resume"]["kept"]] == ["keep0"]


def test_the_next_walk_resumes_where_the_last_one_stopped(meta):
    poly_discover.discover(Pages(PAGES, fail_at=1), "c1")
    http = Pages(PAGES)
    rows, complete = poly_discover.discover(http, "c2")
    assert complete is True
    assert http.cursors[0] == "1", "must not re-walk pages it already has"
    assert {r["market_id"] for r in rows} == {"m-keep0", "m-keep1", "m-keep2"}
    assert meta["discovery_resume"] == {}


def test_a_stale_interrupted_walk_starts_again(meta):
    poly_discover.discover(Pages(PAGES, fail_at=1), "c1")
    meta["discovery_resume"]["at_utc"] = "2026-01-01T00:00:00+00:00"
    http = Pages(PAGES)
    poly_discover.discover(http, "c2")
    assert http.cursors[0] is None


# ------------------------------------------------------------- job ordering


@pytest.fixture
def order(monkeypatch):
    seq = []
    stamps = {}
    monkeypatch.setattr(jobs, "_markets_for_books", lambda run: [])
    monkeypatch.setattr(poly_discover, "tracked_markets", lambda *a, **k: [])
    monkeypatch.setattr(poly_events, "refresh_events", lambda *a, **k: {})
    monkeypatch.setattr(jobs, "read_meta", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "write_meta", lambda name, payload: stamps.__setitem__(name, payload))
    monkeypatch.setattr(jobs, "should_run_kalshi", lambda *a: (True, "due"))
    monkeypatch.setattr(jobs, "is_regular_hours", lambda *a, **k: True)
    monkeypatch.setattr(poly_books, "collect_books",
                        lambda *a, **k: (seq.append("books"), ({}, {}))[1])
    monkeypatch.setattr(kalshi, "collect", lambda *a, **k: (seq.append("kalshi"), {})[1])
    monkeypatch.setattr(options, "collect_options", lambda *a, **k: (seq.append("options"), {})[1])
    return seq, stamps


def test_discovery_runs_after_every_book_phase(order, monkeypatch):
    seq, stamps = order
    monkeypatch.setattr(poly_discover, "discover",
                        lambda *a, **k: (seq.append("discover"), ([{"market_id": "1"}], True))[1])
    run = jobs.Run("snapshot")
    jobs.job_snapshot(run)
    assert seq == ["books", "kalshi", "options", "discover"]
    assert "last_discovery" in stamps and run.status == "ok"


def test_an_unfinished_walk_stays_due_and_does_not_truncate_the_tick(order, monkeypatch):
    seq, stamps = order
    monkeypatch.setattr(poly_discover, "discover", lambda *a, **k: ([], False))
    run = jobs.Run("snapshot")
    jobs.job_snapshot(run)
    assert "last_discovery" not in stamps, "a partial walk must not reset the interval"
    assert run.status == "ok", "books, Kalshi and options were all written"
    assert any("resumes next tick" in n for n in run.notes)
