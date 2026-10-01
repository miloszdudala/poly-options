"""Bucketing, intervals, Brier and the cost of one bet.

The barrier maths and the touch table moved to `test_fair.py` and
`test_empirical.py` when the engine was built. `calibration.py` re-exports them
so the report keeps a single import, but a test belongs next to its code.
"""
import math

import numpy as np
import pytest

import calibration as cal


# ------------------------------------------------------------------ buckets


@pytest.mark.parametrize("price,expected", [
    (0.001, "<3c"), (0.029, "<3c"), (0.03, "3-5c"), (0.049, "3-5c"),
    (0.05, "5-10c"), (0.10, "10-20c"), (0.20, "20-35c"), (0.35, "35-50c"),
    (0.50, "50-65c"), (0.65, ">65c"), (1.0, ">65c"),
])
def test_price_bucket_boundaries_are_left_closed(price, expected):
    assert cal.price_bucket(price) == expected


@pytest.mark.parametrize("price", [None, 0.0, -0.1, 1.5, float("nan")])
def test_price_bucket_rejects_non_prices(price):
    assert cal.price_bucket(price) is None


def test_bucket_labels_matches_the_scalar_version():
    prices = np.array([0.01, 0.04, 0.3, 0.9, 1.5, 0.0, np.nan])
    labels = cal.bucket_labels(prices)
    for price, label in zip(prices, labels):
        assert label == cal.price_bucket(price)


def test_time_buckets_cover_every_horizon():
    assert cal.time_bucket(0.0) == "<4h"
    assert cal.time_bucket(3.9) == "<4h"
    assert cal.time_bucket(4.0) == "4-24h"
    assert cal.time_bucket(24.0) == "1-3d"
    assert cal.time_bucket(5000.0) == ">7d"
    assert cal.time_bucket(-1.0) is None


# --------------------------------------------------------------- intervals


def test_wilson_interval_brackets_the_rate_and_stays_in_zero_one():
    lo, hi = cal.wilson_interval(2, 900)
    assert 0.0 <= lo < 2 / 900 < hi <= 1.0


def test_wilson_interval_narrows_with_more_data():
    small = cal.wilson_interval(5, 20)
    large = cal.wilson_interval(250, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_needs_trials():
    assert all(math.isnan(x) for x in cal.wilson_interval(0, 0))


def test_clustered_interval_is_wider_when_clusters_disagree():
    """The whole point: 40 points from 4 markets are not 40 coin flips."""
    agree = cal.clustered_interval([5, 5, 5, 5], [10, 10, 10, 10])
    disagree = cal.clustered_interval([10, 0, 10, 0], [10, 10, 10, 10])
    assert (disagree[1] - disagree[0]) > (agree[1] - agree[0])


def test_clustered_interval_is_wider_than_binomial_on_clustered_data():
    sums = [10, 0] * 50
    counts = [10] * 100
    clustered = cal.clustered_interval(sums, counts)
    binomial = cal.wilson_interval(sum(sums), sum(counts))
    assert (clustered[1] - clustered[0]) > 3 * (binomial[1] - binomial[0])


def test_clustered_interval_needs_two_clusters():
    assert all(math.isnan(x) for x in cal.clustered_interval([1], [2]))


# ------------------------------------------------------------------ brier


def test_brier_is_zero_for_a_perfect_forecast_and_one_for_a_perfect_miss():
    assert cal.brier([1, 0, 1], [1, 0, 1]) == 0.0
    assert cal.brier([1, 0, 1], [0, 1, 0]) == 1.0


def test_brier_of_a_coin_flip_is_a_quarter():
    assert cal.brier([0.5] * 4, [1, 0, 1, 0]) == pytest.approx(0.25)


def test_brier_ignores_missing_forecasts():
    assert cal.brier([0.5, np.nan], [1, 0]) == pytest.approx(0.25)
    assert math.isnan(cal.brier([np.nan], [1]))


# ------------------------------------------------------------------- bets


def test_cost_to_buy_adds_half_the_spread_then_the_fee_on_what_was_paid():
    # 0.10 mid -> 0.135 paid -> fee 0.04 * 0.135
    assert cal.cost_to_buy(0.10) == pytest.approx(0.135 * 1.04)
    # above 50c the fee is charged on 1 - p
    assert cal.cost_to_buy(0.90) == pytest.approx(0.935 + 0.04 * 0.065)


def test_cost_to_buy_never_exceeds_the_payoff_by_way_of_the_spread():
    assert cal.cost_to_buy(0.99) <= 1.0 + 0.04 * 0.5


def test_leg_pnl_pays_one_on_the_winning_side_and_nothing_on_the_other():
    win, cost = cal.leg_pnl(0.30, True, "yes")
    assert win == pytest.approx(1.0 - cost)
    lose, cost2 = cal.leg_pnl(0.30, False, "yes")
    assert lose == pytest.approx(-cost2)


def test_leg_pnl_prices_the_no_token_at_one_minus_yes():
    """FINDINGS 3.3: a NO ask IS a YES bid; there is no separate vig."""
    _, no_cost = cal.leg_pnl(0.08, False, "no")
    assert no_cost == pytest.approx(cal.cost_to_buy(0.92))


def test_leg_pnl_of_a_cheap_longshot_no_is_a_thin_edge():
    """A 5c YES means NO costs 0.95 + 3.5c + fee; it wins 1.00. Under 2c gross."""
    pnl, cost = cal.leg_pnl(0.05, False, "no")
    assert 0.0 < pnl < 0.02
    assert cost > 0.98


def test_leg_pnl_rejects_an_unknown_side():
    with pytest.raises(ValueError):
        cal.leg_pnl(0.3, True, "both")


def test_leg_pnl_vectorises():
    pnl, cost = cal.leg_pnl(np.array([0.2, 0.8]), np.array([True, False]), "yes")
    assert pnl.shape == (2,) and cost.shape == (2,)


def test_first_signal_takes_the_first_firing_only():
    assert cal.first_signal([False, False, True, True]) == 2
    assert cal.first_signal([True]) == 0
    assert cal.first_signal([False, False]) is None
    assert cal.first_signal([]) is None
