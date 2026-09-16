"""Timestamp, timezone and market-calendar handling.

The collector joins venues fetched minutes apart, so every timestamp must end up
aware and in UTC, and US session boundaries must follow the tz database rather
than a fixed offset (US DST ends 1 November 2026).
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from util import (
    UTC, is_regular_hours, is_trading_day, market_tz, new_cycle_id,
    parse_epoch, parse_iso, utc_iso,
)

ET = ZoneInfo("America/New_York")


# ------------------------------------------------------------ epoch parsing

def test_parse_epoch_units():
    """Polymarket mixes seconds (prices-history) and milliseconds (book timestamp)."""
    seconds = parse_epoch(1789448417)
    millis = parse_epoch("1789448417000")
    micros = parse_epoch(1789448417000000)
    assert seconds == millis == micros
    assert seconds.tzinfo is not None
    assert seconds.utcoffset() == dt.timedelta(0)


def test_parse_epoch_real_clob_book_timestamp():
    """A real CLOB book timestamp is milliseconds and must land in 2026."""
    got = parse_epoch("1789532629085")
    assert got.year == 2026
    assert got.tzinfo == UTC


@pytest.mark.parametrize("bad", [None, "", "0", 0, -5, "abc", "null"])
def test_parse_epoch_rejects_junk(bad):
    assert parse_epoch(bad) is None


# -------------------------------------------------------------- iso parsing

def test_parse_iso_z_suffix_is_utc():
    got = parse_iso("2026-09-30T20:00:00Z")
    assert got == dt.datetime(2026, 9, 30, 20, 0, tzinfo=UTC)


def test_parse_iso_naive_is_assigned_the_given_zone():
    """Cboe publishes a naive ET timestamp: '2026-09-16 03:57:09'."""
    got = parse_iso("2026-09-16 03:57:09", assume_tz=ET)
    # 03:57:09 EDT (UTC-4) == 07:57:09 UTC
    assert got == dt.datetime(2026, 9, 16, 7, 57, 9, tzinfo=UTC)


def test_parse_iso_naive_defaults_to_utc():
    got = parse_iso("2026-09-16T03:57:09")
    assert got == dt.datetime(2026, 9, 16, 3, 57, 9, tzinfo=UTC)


def test_parse_iso_offset_is_converted_not_stripped():
    got = parse_iso("2026-09-15T18:39:10+00:00")
    assert got.hour == 18
    got_et = parse_iso("2026-09-15T14:39:10-04:00")
    assert got_et == got


@pytest.mark.parametrize("bad", [None, "", "not a date", 12345])
def test_parse_iso_rejects_junk(bad):
    assert parse_iso(bad) is None


def test_utc_iso_always_carries_an_offset():
    text = utc_iso(dt.datetime(2026, 9, 16, 12, 0))  # naive input
    assert text.endswith("+00:00")
    assert parse_iso(text).hour == 12


# ------------------------------------------------------- DST correctness

def test_us_dst_transition_is_not_hardcoded():
    """US DST ends 2026-11-01. ET offset must change across that date."""
    before = dt.datetime(2026, 10, 15, 12, 0, tzinfo=ET)
    after = dt.datetime(2026, 11, 15, 12, 0, tzinfo=ET)
    assert before.utcoffset() == dt.timedelta(hours=-4)  # EDT
    assert after.utcoffset() == dt.timedelta(hours=-5)   # EST


def test_market_tz_is_new_york():
    assert str(market_tz()) == "America/New_York"


@pytest.mark.parametrize("when_et,expected", [
    (dt.datetime(2026, 9, 16, 9, 29, tzinfo=ET), False),   # pre-open
    (dt.datetime(2026, 9, 16, 9, 31, tzinfo=ET), True),    # open
    (dt.datetime(2026, 9, 16, 15, 59, tzinfo=ET), True),   # near close
    (dt.datetime(2026, 9, 16, 16, 30, tzinfo=ET), False),  # after close
    (dt.datetime(2026, 9, 19, 12, 0, tzinfo=ET), False),   # Saturday
    (dt.datetime(2026, 12, 25, 12, 0, tzinfo=ET), False),  # Christmas holiday
])
def test_is_regular_hours(when_et, expected):
    assert is_regular_hours(when_et.astimezone(UTC)) is expected


def test_regular_hours_after_dst_change():
    """Post-DST: 10:00 EST is 15:00 UTC and must still count as open."""
    when = dt.datetime(2026, 11, 16, 10, 0, tzinfo=ET)
    assert when.astimezone(UTC).hour == 15
    assert is_regular_hours(when.astimezone(UTC)) is True


@pytest.mark.parametrize("day,expected", [
    (dt.date(2026, 9, 16), True),    # Wednesday
    (dt.date(2026, 9, 19), False),   # Saturday
    (dt.date(2026, 12, 25), False),  # Christmas
    (dt.date(2026, 7, 3), False),    # Independence Day observed
])
def test_is_trading_day(day, expected):
    assert is_trading_day(day) is expected


# ----------------------------------------------------------------- cycles

def test_cycle_id_is_sortable_and_unique():
    early = new_cycle_id(dt.datetime(2026, 9, 16, 9, 30, tzinfo=UTC))
    late = new_cycle_id(dt.datetime(2026, 9, 16, 9, 45, tzinfo=UTC))
    assert early < late
    assert new_cycle_id() != new_cycle_id()
    assert early.startswith("20260916T093000Z-")
