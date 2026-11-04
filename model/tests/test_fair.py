"""The fair-value maths: Black-Scholes, the smile, and the one-touch formula."""
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


def test_delta_is_negative_for_a_down_barrier_and_positive_for_an_up_one():
    up = fair.one_touch_delta(100.0, 108.0, 5 / 252, 0.45, 0.0, "up")
    down = fair.one_touch_delta(100.0, 92.0, 5 / 252, 0.45, 0.0, "down")
    assert up > 0 and down < 0


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
