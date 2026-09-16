"""Cadence guard: collect only while the NYSE regular session is open.

Prediction markets trade overnight and at weekends, but the dataset exists to
align a book against an option chain and options are not quoted then, so an
off-hours book row has nothing to compare with. `schedule.collect_offhours`
turns the old hourly off-hours sampling back on.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import config
from util import UTC, should_run_snapshot

ET = ZoneInfo("America/New_York")


@pytest.mark.parametrize("when_et,expected", [
    # Regular session: every 15-minute tick runs.
    (dt.datetime(2026, 9, 16, 9, 30, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 9, 45, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 10, 15, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 15, 45, tzinfo=ET), True),
    # The close itself is in session; a minute later is not.
    (dt.datetime(2026, 9, 16, 16, 0, tzinfo=ET), True),
    (dt.datetime(2026, 9, 16, 16, 15, tzinfo=ET), False),
    # Outside the session nothing runs, not even on the hour.
    (dt.datetime(2026, 9, 16, 20, 0, tzinfo=ET), False),
    (dt.datetime(2026, 9, 16, 20, 30, tzinfo=ET), False),
    (dt.datetime(2026, 9, 16, 4, 0, tzinfo=ET), False),
    (dt.datetime(2026, 9, 16, 8, 0, tzinfo=ET), False),
])
def test_cadence(when_et, expected):
    assert should_run_snapshot(when_et.astimezone(UTC))[0] is expected


def test_weekend_is_silent():
    """Saturday: the venues are open, the NYSE is not, so we do not collect."""
    sat = dt.datetime(2026, 9, 19, 13, 0, tzinfo=ET)
    assert should_run_snapshot(sat.astimezone(UTC))[0] is False


def test_holiday_is_silent():
    xmas = dt.datetime(2026, 12, 25, 12, 0, tzinfo=ET)
    assert should_run_snapshot(xmas.astimezone(UTC))[0] is False


def test_after_dst_change_session_ticks_still_run():
    """16 Nov 2026 is EST; 10:15 local must still be an in-session tick."""
    when = dt.datetime(2026, 11, 16, 10, 15, tzinfo=ET)
    assert should_run_snapshot(when.astimezone(UTC))[0] is True


def test_offhours_flag_restores_hourly_sampling(monkeypatch):
    """The old behaviour is one config flag away, and still hourly not 15-min."""
    cfg = config.markets_cfg()
    patched = {**cfg, "schedule": {**cfg["schedule"], "collect_offhours": True}}
    monkeypatch.setattr(config, "markets_cfg", lambda: patched)
    import util
    monkeypatch.setattr(util, "markets_cfg", lambda: patched)
    evening = dt.datetime(2026, 9, 16, 20, 0, tzinfo=ET)
    assert should_run_snapshot(evening.astimezone(UTC))[0] is True
    assert should_run_snapshot(evening.replace(minute=30).astimezone(UTC))[0] is False
