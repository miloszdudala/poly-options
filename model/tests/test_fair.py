"""The fair-value engine: Black-Scholes, the smile, and the touch probability.

The four tests MODELS 3.3 requires of the Monte Carlo are the heart of this
file. They exist because the regular-hours path model has no closed form to
check it against in general, so it has to be pinned at the limits where one
exists: `f = 1` must reproduce continuous monitoring exactly, `f = 0` must
reduce to a handful of discrete points, and in between it must move the right
way when volatility, time or distance changes.
"""
import math

import numpy as np
import pandas as pd
import pytest

import fair


def _pool(legs=12, paths=40_000, seed=1):
    return fair.normal_pool(legs, paths=paths, seed=seed)


# --------------------------------------------------------- Black-Scholes


def test_bs_put_call_parity_holds():
    S, K, T, sigma, r = 171.5, 165.0, 0.25, 0.42, 0.04
    call = float(fair.bs_price(S, K, T, sigma, r, True))
    put = float(fair.bs_price(S, K, T, sigma, r, False))
    assert call - put == pytest.approx(S - K * math.exp(-r * T), abs=1e-8)


def test_bs_collapses_to_intrinsic_without_time_or_vol():
    assert float(fair.bs_price(110, 100, 0.0, 0.4, 0.0, True)) == pytest.approx(10.0)
    assert float(fair.bs_price(90, 100, 0.5, 0.0, 0.0, False)) == pytest.approx(10.0)


def test_implied_vol_round_trips_through_the_price():
    for sigma in (0.08, 0.35, 0.62, 1.40):
        for is_call in (True, False):
            price = fair.bs_price(171.5, 165.0, 0.0105, sigma, 0.0396, is_call)
            got = float(fair.implied_vol(price, 171.5, 165.0, 0.0105, 0.0396, is_call))
            assert got == pytest.approx(sigma, abs=1e-6)


def test_implied_vol_round_trips_across_a_whole_smile():
    strikes = np.arange(140.0, 210.0, 2.5)
    vols = 0.45 + 0.004 * (171.5 - strikes) / 10.0
    is_call = strikes >= 171.5
    prices = fair.bs_price(171.5, strikes, 0.02, vols, 0.04, is_call)
    got = fair.implied_vol(prices, 171.5, strikes, 0.02, 0.04, is_call)
    assert np.allclose(got, vols, atol=1e-6)


def test_implied_vol_is_nan_when_the_quote_is_outside_the_no_arbitrage_range():
    """Where a placeholder quote on an untraded contract lands (FINDINGS 4).

    Below intrinsic or above the underlying, no volatility reproduces the
    quote; returning a number anyway is how a broken quote becomes a fair
    value.
    """
    below_intrinsic = fair.implied_vol(10.0, 171.5, 120.0, 0.01, 0.0, True)
    assert math.isnan(float(below_intrinsic))
    assert math.isnan(float(fair.implied_vol(200.0, 171.5, 165.0, 0.01, 0.0, True)))
    assert math.isnan(float(fair.implied_vol(1.0, 171.5, 165.0, 0.0, 0.0, True)))


# ------------------------------------------------------------------ smiles


def _chain(expiry="2026-09-18", spot=171.5, level=0.50, skew=0.004):
    strikes = np.arange(150.0, 195.0, 2.5)
    vols = level + skew * (spot - strikes) / 10.0
    rows = []
    for strike, vol in zip(strikes, vols):
        for option_type in ("put", "call"):
            is_call = option_type == "call"
            rows.append({
                "underlying": "PLTR", "contract_symbol": f"X{strike}{option_type[0]}",
                "option_type": option_type, "expiry": pd.Timestamp(expiry),
                "strike": strike, "open_interest": 500.0, "volume": 10.0,
                "source_iv": vol,
                "mid": float(fair.bs_price(spot, strike, 0.02, vol, 0.04, is_call)),
            })
    return pd.DataFrame(rows)


def test_build_smile_keeps_only_otm_contracts():
    chain = _chain()
    smile = fair.build_smile(chain, 171.5, pd.Timestamp("2026-09-18"), 0.02, 0.0105, 0.04)
    assert smile.n_contracts == len(chain) // 2
    assert np.all(np.diff(smile.log_moneyness) > 0)


def test_build_smile_recovers_the_vol_it_was_built_from():
    smile = fair.build_smile(_chain(level=0.5, skew=0.004), 171.5,
                             pd.Timestamp("2026-09-18"), 0.02, 0.0105, 0.04)
    vol, clamped = smile.vol_at(0.0)
    assert vol == pytest.approx(0.50, abs=0.005)
    assert not clamped


def test_smile_clamps_outside_the_liquid_strike_range_and_says_so():
    smile = fair.build_smile(_chain(), 171.5, pd.Timestamp("2026-09-18"), 0.02, 0.0105, 0.04)
    edge, _ = smile.vol_at(smile.log_moneyness[0])
    far, clamped = smile.vol_at(smile.log_moneyness[0] - 1.0)
    assert clamped and far == pytest.approx(edge)


def test_interp_vol_interpolates_total_variance_not_vol():
    """The point of MODELS 3.2: `w = sigma^2 T` is linear in time, sigma is not."""
    near = fair.build_smile(_chain("2026-09-18", level=0.60), 171.5,
                            pd.Timestamp("2026-09-18"), 0.02, 0.008, 0.04)
    far = fair.build_smile(_chain("2026-10-16", level=0.40), 171.5,
                           pd.Timestamp("2026-10-16"), 0.10, 0.040, 0.04)
    target = 0.5 * (near.years_model + far.years_model)
    sigma, _, source = fair.interp_vol(near, far, target, 0.0)
    assert source == "interpolated"
    w_near, _ = near.total_variance_at(0.0)
    w_far, _ = far.total_variance_at(0.0)
    assert sigma ** 2 * target == pytest.approx(0.5 * (w_near + w_far), rel=1e-6)
    # a vol interpolation would have given the midpoint of 0.60 and 0.40
    assert sigma != pytest.approx(0.50, abs=0.01)


def test_interp_vol_falls_back_to_one_expiry_and_flags_it():
    only = fair.build_smile(_chain(), 171.5, pd.Timestamp("2026-09-18"), 0.02, 0.0105, 0.04)
    sigma, _, source = fair.interp_vol(None, only, 0.0105, 0.0)
    assert source == "single_expiry"
    assert fair.interp_vol(None, None, 0.01, 0.0)[2] == "none"


def test_interp_vol_moves_variance_between_clocks_rather_than_vol():
    """A calendar-clock quote priced on the session clock keeps its variance.

    A weekend carries calendar time and no monitored variance, so the same
    quote is a *higher* vol per monitored session than per calendar day. What
    must not change is `sigma^2 * T`.
    """
    only = fair.build_smile(_chain(), 171.5, pd.Timestamp("2026-09-18"), 0.02, 0.0105, 0.04)
    model_years = 0.0105
    sigma, _, _ = fair.interp_vol(None, only, model_years, 0.0)
    w_quote, _ = only.total_variance_at(0.0)
    assert sigma ** 2 * model_years == pytest.approx(w_quote, rel=1e-9)
    assert sigma > only.vol_at(0.0)[0]          # less time, same variance


def test_pick_expiries_treats_an_expiry_at_the_deadline_as_on_or_after():
    """A Friday barrier and the Friday expiry end at the same instant."""
    closes = [pd.Timestamp("2026-09-18T20:00Z"), pd.Timestamp("2026-09-25T20:00Z")]
    after, before = fair.pick_expiries(closes, pd.Timestamp("2026-09-18T20:00Z"))
    assert after == closes[0] and before is None


def test_pick_expiries_brackets_a_deadline_between_two_expiries():
    closes = [pd.Timestamp("2026-09-18T20:00Z"), pd.Timestamp("2026-09-25T20:00Z"),
              pd.Timestamp("2026-10-02T20:00Z")]
    after, before = fair.pick_expiries(closes, pd.Timestamp("2026-09-30T20:00Z"))
    assert after == closes[2] and before == closes[1]


# ------------------------------------------- MODELS 3.3, the four required


def test_f_one_reproduces_the_continuous_closed_form():
    """(a) With no unobserved gap, the bridge monitors the path continuously."""
    pool = _pool(legs=12, paths=200_000)
    for sigma, n in ((0.30, 5), (0.55, 5), (0.45, 10)):
        mc = fair.touch_probability(100.0, 110.0, float(n), sigma, 0.0, 1.0, True, pool)
        closed = float(fair.one_touch(100.0, 110.0, n / 252.0, sigma, 0.0, "up"))
        assert mc == pytest.approx(closed, abs=0.004)


def test_f_one_reproduces_the_closed_form_for_a_down_barrier():
    pool = _pool(legs=12, paths=200_000)
    mc = fair.touch_probability(100.0, 92.0, 5.0, 0.45, 0.0, 1.0, False, pool)
    closed = float(fair.one_touch(100.0, 92.0, 5 / 252.0, 0.45, 0.0, "down"))
    assert mc == pytest.approx(closed, abs=0.004)


def test_touch_probability_is_monotone_in_sigma_time_and_distance():
    """(b)"""
    pool = _pool()
    base = dict(spot=100.0, rate=0.0, f=0.6, is_up=True, pool=pool)
    low = fair.touch_probability(barrier=110.0, sessions_left=5.0, sigma=0.30, **base)
    high = fair.touch_probability(barrier=110.0, sessions_left=5.0, sigma=0.60, **base)
    assert low < high

    short = fair.touch_probability(barrier=110.0, sessions_left=2.0, sigma=0.45, **base)
    long_ = fair.touch_probability(barrier=110.0, sessions_left=9.0, sigma=0.45, **base)
    assert short < long_

    near = fair.touch_probability(barrier=103.0, sessions_left=5.0, sigma=0.45, **base)
    far = fair.touch_probability(barrier=125.0, sessions_left=5.0, sigma=0.45, **base)
    assert far < near


def test_a_gap_that_opens_beyond_the_barrier_is_a_touch():
    """(c) The unobserved leg still counts, exactly, on every path.

    Deterministic rather than statistical: a pool whose every overnight draw is
    +4 standard deviations puts every path's first open beyond the barrier, so
    the answer is 1.0 with no sampling error to argue about. `f` is near zero,
    so nothing inside the session is monitored - the touch can only have come
    from the gap.
    """
    pool = np.zeros((2, 4, 2))
    pool[:, :, 0] = 4.0                     # every overnight gap jumps up
    p = fair.touch_probability(100.0, 100.5, 3.0, 0.45, 0.0, 0.01, True, pool)
    assert p == 1.0


def test_gaps_alone_touch_far_less_often_than_a_continuous_path():
    """(c) companion, and the whole monitoring correction in one number.

    With `f` near zero the only prints are the eight opens and closes, and a
    driftless walk that starts at the barrier's doorstep fails to exceed it at
    any of `n` discrete points about `1/sqrt(pi*n)` of the time - the ballot
    problem. Continuous monitoring, which watches the path between those
    points, makes the same touch a near certainty. The gap between the two IS
    what MODELS 3.3 says the closed form gets wrong.
    """
    pool = _pool(paths=100_000)
    sigma, n = 4.0, 8
    rate = 0.5 * sigma ** 2                                   # zero log drift
    p = fair.touch_probability(100.0, 100.5, float(n), sigma, rate, 0.02, True, pool)
    cont = float(fair.one_touch(100.0, 100.5, n / 252.0, sigma, rate, "up"))
    assert cont > 0.99
    assert p == pytest.approx(1.0 - 1.0 / math.sqrt(math.pi * n), abs=0.06)
    assert p < cont - 0.1


def test_f_zero_reduces_to_the_discrete_open_and_close():
    """(d) With no monitored variance the only prints are the opens and closes.

    One session, `f = 0`: the whole day's variance arrives in the gap, the
    session itself moves not at all, so `C = O` and the touch probability is
    exactly `P(O beyond B)` - which has a closed form.
    """
    pool = _pool(paths=200_000)
    spot, barrier, sigma = 100.0, 106.0, 0.50
    p = fair.touch_probability(spot, barrier, 1.0, sigma, 0.0, 0.0, True, pool)
    dt = 1.0 / 252.0
    mu = -0.5 * sigma ** 2
    z = (math.log(barrier / spot) - mu * dt) / (sigma * math.sqrt(dt))
    expected = 1.0 - float(fair.norm_cdf(z))
    assert p == pytest.approx(expected, abs=0.004)


def test_f_zero_is_strictly_below_f_one():
    """Monitoring less of the path cannot find more touches."""
    pool = _pool()
    kw = dict(spot=100.0, barrier=108.0, sessions_left=5.0, sigma=0.45, rate=0.0,
              is_up=True, pool=pool)
    assert (fair.touch_probability(f=0.0, **kw)
            < fair.touch_probability(f=0.6, **kw)
            < fair.touch_probability(f=1.0, **kw))


def test_the_continuous_formula_is_an_upper_bound_on_the_monitored_one():
    """MODELS 3.3's whole claim, at the f this dataset actually shows."""
    pool = _pool()
    for barrier, sessions in ((108.0, 5.0), (95.0, 3.0), (120.0, 11.0)):
        is_up = barrier > 100.0
        mc = fair.touch_probability(100.0, barrier, sessions, 0.45, 0.04, 0.6, is_up, pool)
        cont = float(fair.one_touch(100.0, barrier, sessions / 252.0, 0.45, 0.04,
                                    "up" if is_up else "down"))
        assert mc <= cont + 1e-9


# ------------------------------------------------------------- legs, delta


def test_legs_start_with_a_partial_session_when_the_cycle_is_inside_one():
    legs = fair._legs(2.637)
    assert legs[0] == pytest.approx((0.637, False), abs=1e-6)
    assert legs[1:] == [(1.0, True), (1.0, True)]


def test_legs_are_all_full_sessions_when_the_cycle_is_outside_one():
    assert fair._legs(3.0) == [(1.0, True)] * 3
    assert fair._legs(0.0) == []


def test_touch_probability_refuses_a_pool_that_is_too_short():
    with pytest.raises(ValueError, match="normal pool"):
        fair.touch_probability(100.0, 110.0, 30.0, 0.4, 0.0, 0.6, True, _pool(legs=4))


def test_delta_is_negative_for_a_down_barrier_and_positive_for_an_up_one():
    pool = _pool()
    up = fair.touch_delta(100.0, 108.0, 5.0, 0.45, 0.0, 0.6, True, pool)
    down = fair.touch_delta(100.0, 92.0, 5.0, 0.45, 0.0, 0.6, False, pool)
    assert up > 0 and down < 0


def test_delta_survives_the_monte_carlo_noise_it_would_otherwise_drown_in():
    """Common random numbers: without them a 0.5% bump is pure noise.

    The MC standard error at 20k paths is ~0.003; the true effect of a 0.5%
    spot bump here is of the same order, so two independent estimates would
    give a delta whose sign is a coin flip. Sharing the pool makes the
    difference deterministic given the draws.
    """
    kw = dict(spot=100.0, barrier=108.0, sessions_left=5.0, sigma=0.45, rate=0.0,
              f=0.6, is_up=True)
    deltas = [fair.touch_delta(pool=_pool(seed=s), **kw) for s in range(5)]
    assert np.std(deltas) < 0.1 * abs(np.mean(deltas))


# ------------------------------------------------------------------- flags


def test_deep_otm_fires_past_two_and_a_half_standard_moves():
    assert not fair.deep_otm(100.0, 105.0, 0.45, 20 / 252)
    assert fair.deep_otm(100.0, 160.0, 0.45, 2 / 252)


def test_deep_otm_fires_when_there_is_no_vol_to_judge_by():
    assert fair.deep_otm(100.0, 105.0, float("nan"), 0.01)
    assert fair.deep_otm(100.0, 105.0, 0.45, 0.0)


def test_flags_render_only_what_is_set():
    flags = fair.Flags()
    assert flags.as_string() == ""
    flags.set("deep_otm")
    flags.set("stale_book")
    assert flags.as_string() == "deep_otm,stale_book"


def test_flat_vol_is_the_expiry_s_own_vol_whatever_the_deadline_horizon():
    """Review 2026-09-18, the earnings gap: the pre-earnings expiry's vol is
    carried flat, so it does not depend on how far away the deadline is."""
    class Stub:
        years_model = 0.02

        @staticmethod
        def total_variance_at(x):
            return 0.36 ** 2 * 0.02, False

    sigma, clamped, source = fair.flat_vol(Stub(), 0.0)
    assert sigma == pytest.approx(0.36)
    assert clamped is False and source == "pre_earnings_flat"
