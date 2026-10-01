"""Slug construction and history windowing for the Feb-Aug 2026 backfill.

The window splitter has a hard external constraint behind it: Gamma accepts a
`startTs`/`endTs` span of 15 days and rejects 16 with "interval is too long"
(measured 2026-09-18), and `interval=max` returns an EMPTY history for a closed
market, so windowing is the only way to reach any of this data at all.
"""
import datetime as dt

import pytest

import poly_backfill as pb


UTC = dt.timezone.utc


# ------------------------------------------------------------------- slugs


def test_monthly_slugs_are_lower_case_and_hit_ladder_first():
    hit, above = pb.monthly_slugs("PLTR", "august")
    assert hit == "what-price-will-pltr-hit-in-august-2026"
    assert above == "pltr-above-in-august-2026"


def test_weekly_slug_names_the_monday_without_zero_padding():
    assert pb.weekly_slug("AAPL", dt.date(2026, 8, 10)) == "will-aapl-hit-week-of-august-10-2026"
    assert pb.weekly_slug("NVDA", dt.date(2026, 7, 6)) == "will-nvda-hit-week-of-july-6-2026"
    assert pb.weekly_slug("MU", dt.date(2026, 2, 2)) == "will-mu-hit-week-of-february-2-2026"


def test_mondays_are_mondays_and_span_the_window():
    days = pb.mondays()
    assert all(d.weekday() == 0 for d in days)
    assert days[0] == pb.FIRST_MONDAY and days[-1] == pb.LAST_MONDAY
    assert all((b - a).days == 7 for a, b in zip(days, days[1:]))


def test_mondays_rolls_a_non_monday_start_forward():
    days = pb.mondays(dt.date(2026, 2, 4), dt.date(2026, 2, 20))
    assert days == [dt.date(2026, 2, 9), dt.date(2026, 2, 16)]


def test_all_slugs_covers_every_ticker_month_and_week_exactly_once():
    slugs = pb.all_slugs(["AAPL", "MU"])
    assert len(slugs) == len(set(slugs))
    assert len(slugs) == 2 * (2 * len(pb.MONTHS) + len(pb.mondays()))
    assert slugs[0].startswith("what-price-will-aapl")   # monthly ladders first


# ----------------------------------------------------------------- windows


def test_a_short_life_is_one_window():
    start = dt.datetime(2026, 8, 7, 22, tzinfo=UTC)
    end = start + dt.timedelta(days=7)
    windows = pb.history_windows(start, end)
    assert windows == [(int(start.timestamp()), int(end.timestamp()))]


def test_windows_never_exceed_the_cap_gamma_enforces():
    start = dt.datetime(2026, 7, 25, tzinfo=UTC)
    windows = pb.history_windows(start, start + dt.timedelta(days=40))
    assert windows
    assert all((b - a) <= pb.HISTORY_WINDOW_DAYS * 86400 for a, b in windows)
    # 15 days is accepted by the API and 16 is not, so the cap must be under 16.
    assert pb.HISTORY_WINDOW_DAYS < 16


def test_windows_tile_the_whole_life_with_no_gap():
    start = dt.datetime(2026, 7, 25, tzinfo=UTC)
    end = start + dt.timedelta(days=37, hours=3)
    windows = pb.history_windows(start, end)
    assert windows[0][0] == int(start.timestamp())
    assert windows[-1][1] == int(end.timestamp())
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))


def test_a_backwards_or_empty_life_still_asks_for_something():
    when = dt.datetime(2026, 7, 25, tzinfo=UTC)
    assert len(pb.history_windows(when, when)) == 1
    assert len(pb.history_windows(when, when - dt.timedelta(days=2))) == 1


# -------------------------------------------------------------------- life


def _row(**kw):
    base = {"created_at": "2026-07-25T04:01:01.141585Z", "start_date": None,
            "closed_time": "2026-08-04T13:47:42+00:00", "end_date": "2026-09-01T03:59:59.999Z"}
    base.update(kw)
    return base


def test_market_life_prefers_creation_and_the_touch_time():
    start, end = pb.market_life(_row())
    assert start.isoformat().startswith("2026-07-25T03:01")   # an hour of slack
    assert end.isoformat().startswith("2026-08-04T14:47")


def test_market_life_falls_back_to_start_and_end_date():
    start, end = pb.market_life(_row(created_at=None, closed_time=None,
                                     start_date="2026-07-25T04:07:25Z"))
    assert start.day == 25 and end.month == 9


def test_market_life_is_none_without_usable_timestamps():
    assert pb.market_life(_row(created_at=None, start_date=None)) is None
    assert pb.market_life(_row(closed_time=None, end_date=None)) is None


# ------------------------------------------------------- fetching behaviour


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    """Counts calls and replays a scripted sequence of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.responses.pop(0) if self.responses else _Resp(200, {"history": []})


def _fetcher(responses, monkeypatch):
    http = type("H", (), {"session": _Session(responses), "failures": []})()
    fetcher = pb.Fetcher(http, workers=1)
    monkeypatch.setattr(pb.time, "sleep", lambda *_: None)
    return fetcher


def test_a_429_stops_the_run_instead_of_retrying_through_it(monkeypatch):
    """A backfill told to slow down stops; it is not protecting a 15-minute tick."""
    fetcher = _fetcher([_Resp(429)], monkeypatch)
    with pytest.raises(pb.RateLimited):
        fetcher.get("http://x", {}, "id")
    assert fetcher.session.calls == 1
    assert fetcher.stop.is_set()


def test_a_transient_server_error_is_retried(monkeypatch):
    fetcher = _fetcher([_Resp(503), _Resp(200, {"ok": True})], monkeypatch)
    assert fetcher.get("http://x", {}, "id") == {"ok": True}
    assert fetcher.session.calls == 2


def test_a_hard_client_error_is_recorded_and_not_retried(monkeypatch):
    fetcher = _fetcher([_Resp(404)], monkeypatch)
    assert fetcher.get("http://x", {}, "id") is None
    assert fetcher.session.calls == 1
    assert fetcher.http.failures[0]["error"] == "HTTP 404"


def test_history_dedupes_points_that_two_windows_both_return(monkeypatch):
    """Windows tile exactly, but a point on the seam must count once."""
    seam = 1785861600
    fetcher = _fetcher([
        _Resp(200, {"history": [{"t": seam - 3600, "p": 0.4}, {"t": seam + 12, "p": 0.5}]}),
        _Resp(200, {"history": [{"t": seam + 40, "p": 0.5}, {"t": seam + 3600, "p": 0.6}]}),
    ], monkeypatch)
    points = pb.fetch_history(fetcher, "http://clob", "tok",
                              [(seam - 7200, seam), (seam, seam + 7200)])
    assert [p["p"] for p in points] == [0.4, 0.5, 0.6]


def test_fetch_events_separates_an_empty_answer_from_a_failure(monkeypatch):
    fetcher = _fetcher([_Resp(200, [{"slug": "a", "id": "1"}]), _Resp(200, []),
                        _Resp(404)], monkeypatch)
    got = pb.fetch_events(fetcher, "http://gamma", ["a", "b", "c"])
    assert got["a"]["id"] == "1"
    assert got["b"] is None      # Gamma has no such event: stop asking
    assert "c" not in got        # request failed: try again next run
