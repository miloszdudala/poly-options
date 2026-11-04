"""Realised volatility and the intraday variance share (MODELS 3.5, 3.3)."""
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
