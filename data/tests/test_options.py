"""Option symbol parsing and expiry selection."""
import datetime as dt

import pytest

from options import parse_occ, select_expiries


@pytest.mark.parametrize("symbol,expiry,kind,strike", [
    ("AAPL260916C00245000", "2026-09-16", "call", 245.0),
    ("SPXW261218P04500000", "2026-12-18", "put", 4500.0),
    ("TSLA260930C00397500", "2026-09-30", "call", 397.5),
    ("T260116C00012500", "2026-01-16", "call", 12.5),
])
def test_parse_occ(symbol, expiry, kind, strike):
    got = parse_occ(symbol)
    assert got["expiry"] == expiry
    assert got["option_type"] == kind
    assert got["strike"] == strike


@pytest.mark.parametrize("bad", ["garbage", "", None, "AAPL260916X00245000", "AAPL2609C00245000"])
def test_parse_occ_rejects_bad_symbols(bad):
    assert parse_occ(bad) is None


def test_parse_occ_fractional_strike_is_exact():
    """Strikes are thousandths; 397.5 must not come back as 397.49999."""
    assert parse_occ("TSLA260930C00397500")["strike"] == 397.5


ALL = ["2026-09-16", "2026-09-18", "2026-09-25", "2026-10-16", "2026-10-30",
       "2026-11-20", "2026-12-18", "2026-12-31", "2027-01-15"]


def test_selects_everything_inside_45_day_horizon():
    got = select_expiries(ALL, dt.date(2026, 9, 16))
    for near in ["2026-09-16", "2026-09-18", "2026-09-25", "2026-10-16", "2026-10-30"]:
        assert near in got
    assert "2026-11-20" not in got   # 65 days out
    assert "2027-01-15" not in got


def test_includes_december_expiry_nearest_year_end():
    got = select_expiries(ALL, dt.date(2026, 9, 16))
    assert "2026-12-31" in got
    assert "2026-12-18" not in got   # only the nearest one


def test_december_pick_when_only_third_friday_exists():
    got = select_expiries(["2026-09-18", "2026-12-18"], dt.date(2026, 9, 16))
    assert "2026-12-18" in got


def test_past_expiries_are_excluded():
    got = select_expiries(["2026-09-01", "2026-09-16"], dt.date(2026, 9, 16))
    assert "2026-09-01" not in got
    assert "2026-09-16" in got


def test_rolls_to_next_december_once_this_one_has_passed():
    got = select_expiries(["2027-01-15", "2027-12-17"], dt.date(2026, 12, 28))
    assert "2027-12-17" in got


def test_bad_date_strings_are_ignored_not_fatal():
    got = select_expiries(["not-a-date", "2026-09-18", None], dt.date(2026, 9, 16))
    assert got == ["2026-09-18"]


def test_result_is_sorted_and_unique():
    got = select_expiries(ALL + ALL, dt.date(2026, 9, 16))
    assert got == sorted(set(got))
