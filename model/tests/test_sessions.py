"""NYSE session arithmetic, used by the engine.

`sessions_remaining` is the one place where MODELS 3.3's "regular hours only"
becomes a number, and everything downstream - the horizon of the Monte Carlo,
T in Black-Scholes - is scaled by it. A fractional
result also decides whether the Monte Carlo's first leg opens with an overnight
gap, so the fraction is not decoration.
"""
import pandas as pd
import pytest

import sessions as ses


def _sessions() -> pd.DataFrame:
    """Three tidy 09:30-16:00 ET sessions, as UTC (EDT, so 13:30-20:00Z)."""
    days = ["2026-08-10", "2026-08-11", "2026-08-12"]
    return pd.DataFrame({
        "open": pd.to_datetime([f"{d}T13:30:00Z" for d in days], utc=True),
        "close": pd.to_datetime([f"{d}T20:00:00Z" for d in days], utc=True),
    })


def _sessions_left(point: str, deadline: str) -> float:
    return float(ses.sessions_remaining(
        pd.to_datetime([point], utc=True).values,
        pd.to_datetime([deadline], utc=True).values,
        _sessions())[0])


def test_a_point_before_the_open_has_every_session_ahead_of_it():
    assert _sessions_left("2026-08-10T09:00:00Z", "2026-08-12T20:00:00Z") == pytest.approx(3.0)


def test_a_point_inside_a_session_only_counts_the_part_still_to_come():
    # 16:45Z is 3h15 into a 6h30 session: exactly half of it is gone.
    assert _sessions_left("2026-08-10T16:45:00Z", "2026-08-12T20:00:00Z") == pytest.approx(2.5)


def test_an_overnight_point_carries_no_fraction_of_the_session_that_ended():
    assert _sessions_left("2026-08-10T23:00:00Z", "2026-08-12T20:00:00Z") == pytest.approx(2.0)


def test_the_deadline_session_is_included():
    assert _sessions_left("2026-08-12T13:30:00Z", "2026-08-12T20:00:00Z") == pytest.approx(1.0)


def test_nothing_is_left_at_or_after_the_deadline():
    assert _sessions_left("2026-08-12T20:00:00Z", "2026-08-12T20:00:00Z") == 0.0
    assert _sessions_left("2026-08-13T01:00:00Z", "2026-08-12T20:00:00Z") == 0.0


def test_weekend_and_holiday_gaps_do_not_count_as_sessions():
    """Calendar days between sessions carry variance but are not monitored."""
    sess = pd.DataFrame({
        "open": pd.to_datetime(["2026-08-14T13:30:00Z", "2026-08-17T13:30:00Z"], utc=True),
        "close": pd.to_datetime(["2026-08-14T20:00:00Z", "2026-08-17T20:00:00Z"], utc=True),
    })
    got = ses.sessions_remaining(pd.to_datetime(["2026-08-14T21:00:00Z"], utc=True).values,
                                pd.to_datetime(["2026-08-17T20:00:00Z"], utc=True).values, sess)
    assert float(got[0]) == pytest.approx(1.0)   # three calendar days, one session


# ------------------------------------------------------------- deadlines


def test_deadline_close_pulls_a_monthly_end_date_back_to_the_last_session():
    """`2026-10-01T03:59:59Z` is 23:59 ET on 30 September, hours after the close."""
    got = ses.deadline_close(pd.to_datetime(["2026-10-01T03:59:59Z"]).tz_convert(None).values)
    assert pd.Timestamp(got[0]) == pd.Timestamp("2026-09-30T20:00:00")


def test_deadline_close_leaves_a_weekly_end_date_alone():
    got = ses.deadline_close(pd.to_datetime(["2026-09-18T20:00:00Z"]).tz_convert(None).values)
    assert pd.Timestamp(got[0]) == pd.Timestamp("2026-09-18T20:00:00")


def test_deadline_close_is_nat_before_the_calendar_starts():
    got = ses.deadline_close(pd.to_datetime(["2000-01-01T00:00:00Z"]).tz_convert(None).values)
    assert pd.isna(pd.Timestamp(got[0]))


# ------------------------------------------------- the snapshot session filter


@pytest.mark.parametrize("cycle_id, expected", [
    # October: New York is UTC-4, the session is 13:30Z-20:00Z.
    ("20261016T133003Z-x", True),
    ("20261016T131502Z-x", False),
    ("20261016T200001Z-x", False),   # one second past the inclusive 16:00:00 close
    ("20261016T200000Z-x", True),
    # November: UTC-5, the session is 14:30Z-21:00Z. The old 13:30Z-20:00Z
    # filter kept the first two and dropped the last two.
    ("20261102T133000Z-x", False),
    ("20261102T141500Z-x", False),
    ("20261102T143000Z-x", True),
    ("20261102T204500Z-x", True),
    ("20261102T210000Z-x", True),
    ("20261102T211500Z-x", False),
])
def test_in_session_follows_new_york_time_across_the_dst_change(cycle_id, expected):
    import data
    assert data.in_session(cycle_id) is expected
