"""Intraday event refresh: new markets are adopted, outcomes recorded once.

Two measured gaps drove this: Polymarket lists new barriers around 12:40 ET
(never booked on listing day with a 17:30 ET discovery), and closed events
leave the open-events walk before their NO outcomes are ever written.
"""
import json

import poly_books
import poly_discover
import poly_events


def _event(slug, markets, closed=False):
    return {"id": "e1", "slug": slug, "title": "What price will Apple (AAPL) hit in September?",
            "endDate": "2026-10-01T03:59:59.999Z", "closed": closed, "tags": [],
            "markets": markets}


def _market(mid, question, *, closed=False, resolved=False, prices=None, closed_time=None):
    return {"id": mid, "question": question, "groupItemTitle": None,
            "outcomes": json.dumps(["Yes", "No"]), "clobTokenIds": json.dumps([f"t{mid}a", f"t{mid}b"]),
            "closed": closed, "umaResolutionStatus": "resolved" if resolved else "",
            "outcomePrices": json.dumps(prices) if prices else json.dumps(["0.3", "0.7"]),
            "closedTime": closed_time, "createdAt": "2026-09-17T16:41:15.688253Z",
            "endDate": "2026-10-01T03:59:59.999Z", "acceptingOrders": not closed}


def test_result_is_the_label_priced_at_one():
    m = _market("1", "Will Apple (AAPL) hit (HIGH) $360 in September?", closed=True, resolved=True, prices=["1", "0"])
    assert poly_discover.resolution_result(m, ["Yes", "No"]) == "Yes"
    m["outcomePrices"] = json.dumps(["0", "1"])
    assert poly_discover.resolution_result(m, ["Yes", "No"]) == "No"


def test_result_is_none_until_uma_resolves_and_for_fifty_fifty():
    m = _market("1", "q", closed=True, resolved=False, prices=["1", "0"])
    assert poly_discover.resolution_result(m, ["Yes", "No"]) is None
    m = _market("1", "q", closed=True, resolved=True, prices=["0.5", "0.5"])
    assert poly_discover.resolution_result(m, ["Yes", "No"]) is None


def test_market_row_carries_lifecycle_fields():
    row = poly_discover.market_row(_event("s", []), _market("7", "Will Apple (AAPL) hit (LOW) $304 in September?",
                                   closed=True, resolved=True, prices=["1", "0"], closed_time="2026-09-17 17:26:54+00"), "cyc")
    assert row["contract_type"] == "hit_down" and row["barrier"] == 304.0
    assert row["result"] == "Yes" and row["closed_time"] == "2026-09-17 17:26:54+00"
    assert row["created_at"].startswith("2026-09-17T16:41")
    res = poly_books.resolution_row(row, "cyc")
    assert res["settled_at_utc"] == "2026-09-17T17:26:54+00:00"
    assert res["result"] == "Yes"


def test_refresh_adopts_new_markets_and_records_each_outcome_once(monkeypatch):
    meta = {"poly_markets_latest": {"cycle_id": "c0", "markets": [
        poly_discover.market_row(_event("s", []), _market("1", "Will Apple (AAPL) hit (HIGH) $360 in September?"), "c0")]},
            "tracked_events": {"events": {"s": {"first_seen_utc": "x", "done": False}}}}
    written = {}
    monkeypatch.setattr(poly_events, "read_meta", lambda name: meta.get(name))
    monkeypatch.setattr(poly_events, "write_meta", lambda name, payload: meta.__setitem__(name, payload))
    monkeypatch.setattr(poly_events, "write_table", lambda t, rows: written.setdefault(t, []).extend(rows))

    class FakeHttp:
        def __init__(self, event):
            self.event = event

        def get(self, url, **kw):
            return [self.event]

    # Tick 1: the old market has touched, and a new barrier was listed.
    ev = _event("s", [
        _market("1", "Will Apple (AAPL) hit (HIGH) $360 in September?", closed=True, resolved=True,
                prices=["1", "0"], closed_time="2026-09-17 17:26:54+00"),
        _market("2", "Will Apple (AAPL) hit (LOW) $300 in September?"),
    ])
    counts = poly_events.refresh_events(FakeHttp(ev), "c1")
    assert counts == {"events_refreshed": 1, "markets_new": 1, "markets_resolved": 1}
    assert [r["market_id"] for r in written["poly_markets"]] == ["2"]
    assert written["resolutions"][0]["market_id"] == "1" and written["resolutions"][0]["result"] == "Yes"
    assert len(meta["poly_markets_latest"]["markets"]) == 2
    assert meta["tracked_events"]["events"]["s"]["done"] is False

    # Tick 2: nothing changed - no duplicate resolution row, no new market.
    counts = poly_events.refresh_events(FakeHttp(ev), "c2")
    assert counts == {"events_refreshed": 1, "markets_new": 0, "markets_resolved": 0}
    assert len(written["resolutions"]) == 1

    # Month end: everything resolved -> event retired and pruned from the cache.
    ev["closed"] = True
    ev["markets"][1].update(closed=True, umaResolutionStatus="resolved", outcomePrices=json.dumps(["0", "1"]))
    counts = poly_events.refresh_events(FakeHttp(ev), "c3")
    # Retiring rewrites every outcome of the event (see the test below).
    assert counts["markets_resolved"] == 2
    assert {r["market_id"]: r["result"] for r in written["resolutions"][-2:]} == {"1": "Yes", "2": "No"}
    assert meta["tracked_events"]["events"]["s"]["done"] is True
    assert meta["poly_markets_latest"]["markets"] == []


def test_retiring_event_records_outcomes_the_cache_already_had(monkeypatch):
    """2026-09-25: discovery cached the NO results first; refresh then retired
    the event and pruned it, and the outcomes never reached `resolutions`."""
    done = _market("1", "Will Apple (AAPL) hit (HIGH) $360 in September?", closed=True, resolved=True,
                   prices=["0", "1"], closed_time="2026-10-01 00:00:00+00")
    meta = {"poly_markets_latest": {"markets": [poly_discover.market_row(_event("s", []), done, "c0")]},
            "tracked_events": {"events": {"s": {"first_seen_utc": "x", "done": False}}}}
    written = {}
    monkeypatch.setattr(poly_events, "read_meta", lambda name: meta.get(name))
    monkeypatch.setattr(poly_events, "write_meta", lambda name, payload: meta.__setitem__(name, payload))
    monkeypatch.setattr(poly_events, "write_table", lambda t, rows: written.setdefault(t, []).extend(rows))

    class FakeHttp:
        def get(self, url, **kw):
            return [_event("s", [done], closed=True)]

    poly_events.refresh_events(FakeHttp(), "c1")
    assert [r["result"] for r in written["resolutions"]] == ["No"]
    assert meta["tracked_events"]["events"]["s"]["done"] is True


def test_missing_event_is_retired_after_repeated_misses(monkeypatch):
    meta = {"poly_markets_latest": {"markets": []},
            "tracked_events": {"events": {"gone": {"first_seen_utc": "x", "done": False}}}}
    monkeypatch.setattr(poly_events, "read_meta", lambda name: meta.get(name))
    monkeypatch.setattr(poly_events, "write_meta", lambda name, payload: meta.__setitem__(name, payload))
    monkeypatch.setattr(poly_events, "write_table", lambda *a, **k: None)

    class Nothing:
        def get(self, url, **kw):
            return []

    for _ in range(8):
        poly_events.refresh_events(Nothing(), "c")
    assert meta["tracked_events"]["events"]["gone"]["done"] is True


def test_a_failed_request_is_not_a_miss(monkeypatch):
    """2026-09-29: a three-hour Gamma outage retired 204 live events."""
    meta = {"poly_markets_latest": {"markets": []},
            "tracked_events": {"events": {"live": {"event_id": "1", "first_seen_utc": "x", "done": False},
                                          "by_slug": {"first_seen_utc": "x", "done": False}}}}
    monkeypatch.setattr(poly_events, "read_meta", lambda name: meta.get(name))
    monkeypatch.setattr(poly_events, "write_meta", lambda name, payload: meta.__setitem__(name, payload))
    monkeypatch.setattr(poly_events, "write_table", lambda *a, **k: None)

    class Down:
        def get(self, url, **kw):
            return None   # what Http returns when it gives up

    for _ in range(20):
        poly_events.refresh_events(Down(), "c")
    for entry in meta["tracked_events"]["events"].values():
        assert entry["done"] is False and not entry.get("misses")


def test_known_ids_are_fetched_in_batches_of_100(monkeypatch):
    """128 events must cost 2 requests, not 128: the laptop's IP is not a bot farm."""
    tracked = {f"s{i}": {"event_id": str(i), "done": False} for i in range(128)}
    meta = {"poly_markets_latest": {"markets": []}, "tracked_events": {"events": tracked}}
    monkeypatch.setattr(poly_events, "read_meta", lambda name: meta.get(name))
    monkeypatch.setattr(poly_events, "write_meta", lambda name, payload: meta.__setitem__(name, payload))
    monkeypatch.setattr(poly_events, "write_table", lambda *a, **k: None)
    calls = []

    class Http:
        def get(self, url, params=None, **kw):
            calls.append(params)
            ids = [v for k, v in params if k == "id"]
            assert len(ids) <= 100
            return [{"id": i, "slug": f"s{i}", "title": "x", "markets": []} for i in ids]

    counts = poly_events.refresh_events(Http(), "c")
    assert len(calls) == 2 and counts["events_refreshed"] == 128
