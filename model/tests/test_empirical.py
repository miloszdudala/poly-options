"""The historical touch table and the intraday variance share (MODELS 3.5, 3.3).

The unit trap in `touch_table` (annualised vs daily sigma) gets its own test
because getting it wrong is silent: the table still builds, the lookup still
returns a number, and every fair value it produces reads 0.000.
"""
import math

import numpy as np
import pytest

import empirical as emp

# ------------------------------------------------------------- volatility


def test_ewma_vol_is_annualised_and_tracks_a_regime_change():
    rng = np.random.default_rng(3)
    quiet = 0.005 * rng.standard_normal(400)
    loud = 0.04 * rng.standard_normal(200)
    closes = 100 * np.exp(np.cumsum(np.concatenate([quiet, loud])))
    vol = emp.ewma_vol(closes)
    assert math.isnan(vol[0])
    assert vol[399] < 0.25 < vol[-1]
    # 0.005 daily is ~8% annualised, 0.04 daily is ~63%.
    assert 0.02 < vol[399] < 0.20
    assert 0.35 < vol[-1] < 0.95


def test_ewma_vol_handles_a_short_series():
    assert np.isnan(emp.ewma_vol([100.0])).all()


# --------------------------------------------------- empirical touch table


def _random_walk(n=900, daily_vol=0.02, seed=5):
    rng = np.random.default_rng(seed)
    rets = daily_vol * rng.standard_normal(n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * np.exp(np.abs(0.4 * daily_vol * rng.standard_normal(n)))
    low = close * np.exp(-np.abs(0.4 * daily_vol * rng.standard_normal(n)))
    return high, low, close


def test_touch_table_decreases_in_k_and_increases_in_h():
    high, low, close = _random_walk()
    table = emp.touch_table(high, low, close)
    row = table.up[table.hs.tolist().index(5)]
    assert np.all(np.diff(row) <= 1e-12)
    far = table.ks.tolist().index(1.5)
    assert table.up[0, far] < table.up[-1, far]


def test_touch_table_at_k_zero_is_the_row_maximum_but_not_certain():
    """A barrier AT spot is not a free touch, and that is MODELS 3.3's point.

    Only the regular session is watched, so a session that gaps down and never
    trades back up never prints the previous close. k = 0 is therefore the
    easiest barrier in the row but still well under 1.
    """
    high, low, close = _random_walk()
    table = emp.touch_table(high, low, close)
    assert table.up[0, 0] == table.up[0].max()
    assert table.down[0, 0] == table.down[0].max()
    assert 0.4 < table.up[0, 0] < 1.0
    assert table.up[-1, 0] > table.up[0, 0]  # more sessions, more chances


def test_touch_table_expects_daily_sigma_not_annualised():
    """The unit trap. Annualised sigma collapses the table to zeros."""
    high, low, close = _random_walk()
    good = emp.touch_table(high, low, close)                       # daily, by default
    bad = emp.touch_table(high, low, close, sigma=emp.ewma_vol(close))
    k1 = good.ks.tolist().index(1.0)
    assert good.up[4, k1] > 0.15
    assert bad.up[4, k1] < 0.01


def test_touch_table_lookup_interpolates_and_clamps():
    high, low, close = _random_walk()
    table = emp.touch_table(high, low, close)
    k1, k2 = table.ks.tolist().index(1.0), table.ks.tolist().index(1.25)
    mid = table.lookup(1.125, 5, "up")
    assert min(table.up[4, k1], table.up[4, k2]) <= mid <= max(table.up[4, k1], table.up[4, k2])
    assert table.lookup(99.0, 5, "up") == pytest.approx(table.up[4, -1])
    assert table.lookup(1.0, 0.1, "up") == pytest.approx(table.up[0, k1])
    assert table.lookup(1.0, 999, "up") == pytest.approx(table.up[-1, k1])


def test_touch_table_lookup_many_splits_by_direction():
    high, low, close = _random_walk()
    table = emp.touch_table(high, low, close)
    got = table.lookup_many([1.0, 1.0], [5, 5], [True, False])
    assert got[0] == pytest.approx(table.lookup(1.0, 5, "up"))
    assert got[1] == pytest.approx(table.lookup(1.0, 5, "down"))


def test_touch_table_counts_the_windows_it_used():
    high, low, close = _random_walk(n=300)
    table = emp.touch_table(high, low, close)
    assert table.counts[0] > table.counts[-1] > 0
    assert table.counts[0] <= 300



# ------------------------------------------------- intraday variance share (f)


def _split_series(n=800, gap_vol=0.01, day_vol=0.02, seed=7):
    """Closes and opens built from a known gap/session variance split."""
    rng = np.random.default_rng(seed)
    close = [100.0]
    opens = []
    for _ in range(n):
        o = close[-1] * math.exp(gap_vol * rng.standard_normal())
        c = o * math.exp(day_vol * rng.standard_normal())
        opens.append(o)
        close.append(c)
    return np.array(opens), np.array(close[1:]), np.array(close[:-1])


def test_intraday_share_recovers_a_known_split():
    opens, closes, prev = _split_series(gap_vol=0.01, day_vol=0.02)
    all_closes = np.concatenate([[prev[0]], closes])
    all_opens = np.concatenate([[opens[0]], opens])
    f = emp.intraday_variance_share(all_opens, all_closes)
    expected = 0.02 ** 2 / (0.01 ** 2 + 0.02 ** 2)   # 0.8
    assert f == pytest.approx(expected, abs=0.05)


def test_intraday_share_is_one_when_nothing_happens_overnight():
    opens, closes, prev = _split_series(gap_vol=1e-9, day_vol=0.02)
    f = emp.intraday_variance_share(np.concatenate([[opens[0]], opens]),
                                    np.concatenate([[prev[0]], closes]))
    assert f > 0.99


def test_intraday_share_is_near_zero_when_everything_gaps():
    opens, closes, prev = _split_series(gap_vol=0.03, day_vol=1e-9)
    f = emp.intraday_variance_share(np.concatenate([[opens[0]], opens]),
                                    np.concatenate([[prev[0]], closes]))
    assert f < 0.01


def test_intraday_share_refuses_to_guess_from_too_little_data():
    assert math.isnan(emp.intraday_variance_share([100.0, 101.0], [100.5, 101.5]))
    assert math.isnan(emp.intraday_variance_share([100.0] * 80, [100.0] * 79))
