"""The fair-value maths: Black-Scholes, the smile, and the touch probability."""
import math

import numpy as np
import pandas as pd
import pytest

import fair


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


def test_expiry_vol_moves_variance_between_clocks_rather_than_vol():
    """A calendar-clock quote priced on the session clock keeps its variance.

    A weekend carries calendar time and no monitored variance, so the same
    quote is a *higher* vol per monitored session than per calendar day. What
    must not change is `sigma^2 * T`.
    """
    smile = fair.build_smile(_chain(), 171.5, pd.Timestamp("2026-09-18"), 0.02, 0.0105, 0.04)
    sigma, clamped = fair.expiry_vol(smile, 0.0)
    w_quote, _ = smile.total_variance_at(0.0)
    assert sigma ** 2 * smile.years_model == pytest.approx(w_quote, rel=1e-9)
    assert sigma > smile.vol_at(0.0)[0]          # less time, same variance
    assert clamped is False


def test_expiry_vol_is_nan_without_a_session_clock():
    class Stub:
        years_model = 0.0

        @staticmethod
        def total_variance_at(x):
            return 0.01, False

    assert np.isnan(fair.expiry_vol(Stub(), 0.0)[0])


def test_an_expiry_at_the_deadline_counts_as_on_or_after():
    """A Friday barrier and the Friday expiry end at the same instant."""
    closes = [pd.Timestamp("2026-09-25T20:00Z"), pd.Timestamp("2026-09-18T20:00Z")]
    assert (fair.first_expiry_at_or_after(closes, pd.Timestamp("2026-09-18T20:00Z"))
            == closes[1])


def test_the_first_expiry_after_a_deadline_between_two_is_the_later_one():
    closes = [pd.Timestamp("2026-09-18T20:00Z"), pd.Timestamp("2026-09-25T20:00Z"),
              pd.Timestamp("2026-10-02T20:00Z")]
    assert (fair.first_expiry_at_or_after(closes, pd.Timestamp("2026-09-30T20:00Z"))
            == closes[2])
    assert fair.first_expiry_at_or_after(closes, pd.Timestamp("2026-10-05T20:00Z")) is None


# ------------------------------------------------------------ one-touch


def test_one_touch_matches_the_reflection_principle_with_no_drift():
    """With zero log drift (r = sigma^2 / 2), P(touch) = 2 N(-b / (sigma sqrt T))."""
    sigma, years = 0.45, 5 / 252
    rate = 0.5 * sigma ** 2
    b = math.log(110.0 / 100.0)
    expected = 2.0 * float(fair.norm_cdf(-b / (sigma * math.sqrt(years))))
    assert float(fair.one_touch(100.0, 110.0, years, sigma, rate, "up")) == pytest.approx(expected)
    assert float(fair.one_touch(100.0, 100.0 / 1.1, years, sigma, rate, "down")) == pytest.approx(expected)


def test_one_touch_is_one_at_or_beyond_the_barrier_and_zero_without_time():
    assert float(fair.one_touch(100.0, 100.0, 0.02, 0.4, 0.0, "up")) == 1.0
    assert float(fair.one_touch(100.0, 105.0, 0.02, 0.4, 0.0, "down")) == 1.0
    assert float(fair.one_touch(100.0, 110.0, 0.0, 0.4, 0.0, "up")) == 0.0


def test_one_touch_is_monotone_in_sigma_time_and_distance():
    p = lambda barrier=110.0, years=5 / 252, sigma=0.45: float(  # noqa: E731
        fair.one_touch(100.0, barrier, years, sigma, 0.0, "up"))
    assert p(sigma=0.30) < p(sigma=0.60)
    assert p(years=2 / 252) < p(years=9 / 252)
    assert p(barrier=125.0) < p(barrier=103.0)


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
    assert flags.columns()["flags"] == "deep_otm,stale_book"
    assert flags.columns()["no_chain"] is False


def test_an_unknown_flag_is_an_error_not_a_new_attribute():
    with pytest.raises(AttributeError):
        fair.Flags().set("clamped_strike")


# ---------------------------------------------- regular-hours touch


def _pool(legs=12, paths=40_000, seed=1):
    return fair.normal_pool(legs, paths=paths, seed=seed)


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
    what INSIGHTS 3.3 says the closed form gets wrong.
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
    """INSIGHTS 3.3's whole claim, at the f this dataset actually shows."""
    pool = _pool()
    for barrier, sessions in ((108.0, 5.0), (95.0, 3.0), (120.0, 11.0)):
        is_up = barrier > 100.0
        mc = fair.touch_probability(100.0, barrier, sessions, 0.45, 0.04, 0.6, is_up, pool)
        cont = float(fair.one_touch(100.0, barrier, sessions / 252.0, 0.45, 0.04,
                                    "up" if is_up else "down"))
        assert mc <= cont + 1e-9


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
    f = fair.intraday_variance_share(all_opens, all_closes)
    expected = 0.02 ** 2 / (0.01 ** 2 + 0.02 ** 2)   # 0.8
    assert f == pytest.approx(expected, abs=0.05)


def test_intraday_share_is_one_when_nothing_happens_overnight():
    opens, closes, prev = _split_series(gap_vol=1e-9, day_vol=0.02)
    f = fair.intraday_variance_share(np.concatenate([[opens[0]], opens]),
                                    np.concatenate([[prev[0]], closes]))
    assert f > 0.99


def test_intraday_share_is_near_zero_when_everything_gaps():
    opens, closes, prev = _split_series(gap_vol=0.03, day_vol=1e-9)
    f = fair.intraday_variance_share(np.concatenate([[opens[0]], opens]),
                                    np.concatenate([[prev[0]], closes]))
    assert f < 0.01


def test_intraday_share_refuses_to_guess_from_too_little_data():
    assert math.isnan(fair.intraday_variance_share([100.0, 101.0], [100.5, 101.5]))
    assert math.isnan(fair.intraday_variance_share([100.0] * 80, [100.0] * 79))
