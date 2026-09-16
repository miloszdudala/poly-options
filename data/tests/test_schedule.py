"""Cadence guard: 15-minute snapshots in session, hourly outside it."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from util import UTC, should_run_snapshot

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("when_et,expected", [
    # Regular session: every 15-minute tick runs.
    (dt.datetime(2026, 9, 16, 9, 30, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 9, 45, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 10, 15, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 15, 45, tzinfo=ET), True),
    # Outside the session: only the top-of-hour tick runs.
    (dt.datetime(2026, 9, 16, 20, 0, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 20, 15, tzinfo=ET), False),
    (dt.datetime(2026, 9, 16, 20, 30, tzinfo=ET), False),
    (dt.datetime(2026, 9, 16, 20, 45, tzinfo=ET), False),
    (dt.datetime(2026, 9, 16, 4, 0, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 4, 30, tzinfo=ET), False),
])
def test_cadence(when_et, expected):
    assert should_run_snapshot(when_et.astimezone(UTC))[0] is expected


def test_weekend_still_collects_hourly():
    """Prediction markets trade at weekends even though the NYSE does not."""
    sat = dt.datetime(2026, 9, 19, 13, 0, tzinfo=ET)
    assert should_run_snapshot(sat.astimezone(UTC))[0] is True
    assert should_run_snapshot(sat.replace(minute=30).astimezone(UTC))[0] is False


def test_holiday_is_treated_as_off_hours():
    xmas = dt.datetime(2026, 12, 25, 12, 0, tzinfo=ET)
    assert should_run_snapshot(xmas.astimezone(UTC))[0] is True          # hourly
    assert should_run_snapshot(xmas.replace(minute=15).astimezone(UTC))[0] is False


def test_after_dst_change_session_ticks_still_run():
    """16 Nov 2026 is EST; 10:15 local must still be an in-session tick."""
    when = dt.datetime(2026, 11, 16, 10, 15, tzinfo=ET)
    assert should_run_snapshot(when.astimezone(UTC))[0] is True
