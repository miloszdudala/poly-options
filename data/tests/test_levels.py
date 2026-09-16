"""Strike filtering.

The point of the filter is to drop strikes nothing references without ever
dropping one a tracked market needs, so the tests are built from real spreads:
NFLX levels run -74% to +80% of spot and OPEN's to +279%, which is exactly where
a naive fixed band around spot fails.
"""
import pytest

from levels import keep_strike


def keep(strike, spot, levels, band=0.30, margin=0.10):
    return keep_strike(strike, spot, set(levels), band, margin)


def test_keeps_strikes_near_spot():
    assert keep(100, 100, []) is True
    assert keep(125, 100, []) is True     # +25%, inside the band
    assert keep(75, 100, []) is True


def test_drops_far_strikes_nothing_references():
    """A $5 and a $990 strike on a $356 stock are noise."""
    assert keep(5, 356, [400]) is False
    assert keep(990, 356, [400]) is False


def test_keeps_far_strikes_that_a_tracked_market_needs():
    """NFLX trades at 78 with tracked levels out to 140 - those must survive."""
    assert keep(140, 78, [140]) is True
    assert keep(20, 78, [20]) is True
    # ...while an untracked strike equally far out does not.
    assert keep(300, 78, [20, 140]) is False


def test_open_style_extreme_levels_survive():
    """OPEN trades at 2.60 with a tracked level at 10.00 (+279%)."""
    assert keep(10.0, 2.60, [10.0]) is True
    assert keep(9.5, 2.60, [10.0]) is True    # within 10% of the level
    assert keep(50.0, 2.60, [10.0]) is False


def test_neighbouring_strikes_survive_for_interpolation():
    """Pricing a level needs the strikes either side of it, not just the exact one."""
    for strike in (395, 397.5, 400, 405):
        assert keep(strike, 356, [400]) is True, strike


def test_spx_wings_are_dropped_but_tracked_levels_are_not():
    spot, levels = 7585.0, [4000.0, 7745.0, 10600.0]
    assert keep(200, spot, levels) is False      # the useless tail
    assert keep(13000, spot, levels) is False
    assert keep(7745, spot, levels) is True      # a Kalshi strike
    assert keep(4000, spot, levels) is True      # year-end ladder
    assert keep(10600, spot, levels) is True


@pytest.mark.parametrize("spot,levels", [(None, [400]), (0, [400])])
def test_missing_spot_still_honours_levels(spot, levels):
    assert keep(400, spot, levels) is True
    assert keep(5, spot, levels) is False


def test_none_strike_is_not_kept_by_accident():
    assert keep(None, 100, [100]) is False


class TestStrikeFilterObject:
    def test_keeps_everything_when_disabled(self):
        from levels import StrikeFilter

        f = StrikeFilter()
        f.enabled = False
        assert f.accepts("TSLA", 5.0, 356.0) is True

    def test_unknown_symbol_with_no_levels_and_no_spot_is_kept(self):
        """Never silently drop data we have no basis to judge."""
        from levels import StrikeFilter

        f = StrikeFilter()
        f.enabled = True
        f.levels = {}
        assert f.accepts("WHAT", 123.0, None) is True

    def test_counts_are_tracked(self):
        from levels import StrikeFilter

        f = StrikeFilter()
        f.enabled = True
        f.levels = {"TSLA": {400.0}}
        f.accepts("TSLA", 400.0, 356.0)
        f.accepts("TSLA", 5.0, 356.0)
        assert f.kept == 1 and f.dropped == 1
        assert "50%" in f.summary()
