"""Kalshi (fee, books, payoff, pricing) and WTI (Active Month, roll, moneyness),
on synthetic data."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import data
import fair
import run
import sim
from models import NO, YES
from sessions import nyse_sessions

run.discover_types()        # puts model/types on sys.path
import _kalshi  # noqa: E402
import _wti  # noqa: E402

SESSIONS = nyse_sessions()

# ------------------------------------------------------------------ Kalshi


def test_the_index_fee_is_kalshis_standard_rate():
    bet_type = run.discover_types()["kalshi-index-daily-sp500"]()
    assert bet_type.fee(0.50) == pytest.approx(0.07 * 0.25)
    assert bet_type.fee(0.90) == pytest.approx(0.07 * 0.09)
    assert bet_type.fee(0.10) == pytest.approx(bet_type.fee(0.90))


def test_yes_is_bought_from_the_no_bids_and_no_from_the_yes_bids():
    bids = pd.DataFrame([
        {"cycle_id": "c", "fetched_at_utc": "2026-09-16T15:00:00Z", "ticker": "T1",
         "side": "no", "depth_rank": 0, "price_prob": 0.40, "size": 10.0},
        {"cycle_id": "c", "fetched_at_utc": "2026-09-16T15:00:00Z", "ticker": "T1",
         "side": "no", "depth_rank": 1, "price_prob": 0.38, "size": 5.0},
        {"cycle_id": "c", "fetched_at_utc": "2026-09-16T15:00:00Z", "ticker": "T1",
         "side": "yes", "depth_rank": 0, "price_prob": 0.55, "size": 7.0},
    ])
    asks = _kalshi.kalshi_asks(bids)
    yes = asks[asks["outcome_index"] == 0]
    assert list(yes["price"]) == pytest.approx([0.60, 0.62])
    assert list(yes["depth_rank"]) == [0, 1] and list(yes["size"]) == [10.0, 5.0]
    no = asks[asks["outcome_index"] == 1]
    assert list(no["price"]) == pytest.approx([0.45])


@pytest.mark.parametrize("term,expected,direction", [
    ({"strike_type": "greater", "floor_strike": 6000.0}, 0.30, "up"),
    ({"strike_type": "less", "cap_strike": 6000.0}, 0.70, "down"),
    ({"strike_type": "between", "floor_strike": 5990.0, "cap_strike": 6000.0}, 0.10, None),
    ({"strike_type": "custom"}, None, None),
])
def test_the_payoff_follows_the_strike_type(term, expected, direction):
    above = {5990.0: 0.40, 6000.0: 0.30}.get
    got, lean = _kalshi.fair_yes(term, above)
    assert (got is None) == (expected is None)
    if expected is not None:
        assert got == pytest.approx(expected)
    assert lean == direction


def test_kalshis_levels_are_read_to_the_cent():
    seen = []
    _kalshi.fair_yes({"strike_type": "between", "floor_strike": 7550.0, "cap_strike": 7574.9999},
                     lambda k: seen.append(k) or 0.5)
    assert seen == [7550.0, 7575.0]


def test_a_kalshi_result_settles_at_par_or_nothing():
    assert sim.payout_per_share("Yes", None, YES) == 1.0
    assert sim.payout_per_share("Yes", None, NO) == 0.0


NOW = pd.Timestamp("2026-09-16T15:00:00Z")
CLOSE = pd.Timestamp("2026-09-16T20:00:00Z")
SPOT, SIGMA, RATE = 6000.0, 0.15, 0.04


def spx_calls(years):
    strikes = np.arange(5900.0, 6100.01, 1.0)
    return pd.concat([
        pd.DataFrame({"underlying": "SPX", "option_type": kind, "contract_symbol": "SPXW260916",
                      "expiry": pd.Timestamp("2026-09-16"), "strike": strikes,
                      "mid": fair.bs_price(SPOT, strikes, years, SIGMA, RATE, kind == "call")})
        for kind in ("call", "put")], ignore_index=True)


def test_a_range_at_the_close_is_priced_from_the_spx_call_spread(monkeypatch):
    years = (CLOSE - NOW).total_seconds() / (365 * 86400)
    bet_type = run.discover_types()["kalshi-index-daily-sp500"]()
    asks = pd.DataFrame([{"cycle_id": "c", "fetched_at_utc": NOW, "market_id": t,
                          "outcome_index": 0, "side": "ask", "depth_rank": 0,
                          "price": 0.5, "size": 10.0} for t in ("AT_CLOSE", "AT_NOON")])
    monkeypatch.setattr(bet_type, "books", lambda cycle_id: asks)
    bet_type._terms = pd.DataFrame([
        {"ticker": "AT_CLOSE", "strike_type": "between", "floor_strike": 5990.0,
         "cap_strike": 6010.0, "close_time": CLOSE},
        {"ticker": "AT_NOON", "strike_type": "greater", "floor_strike": 6000.0,
         "cap_strike": None, "close_time": pd.Timestamp("2026-09-16T16:00:00Z")},
    ]).set_index("ticker")
    monkeypatch.setattr(data, "option_chain", lambda cycle_id, symbols: spx_calls(years))
    monkeypatch.setattr(data, "quote_times", lambda cycle_id: pd.Series({"SPX": NOW},
                                                                         dtype="object"))
    monkeypatch.setattr(data, "risk_free_rate", lambda when: RATE)
    out = bet_type.priced("20260916T150000Z-c")

    def n_d2(k):
        d2 = (math.log(SPOT / k) + (RATE - 0.5 * SIGMA ** 2) * years) / (SIGMA * math.sqrt(years))
        return float(fair.norm_cdf(d2))

    assert list(out["market_id"]) == ["AT_CLOSE"]
    assert out.iloc[0]["fair"] == pytest.approx(n_d2(5990.0) - n_d2(6010.0), abs=3e-3)
    assert bet_type.skipped == {"no expiry at the deadline": 1}
    assert "hourly" in bet_type.note


# --------------------------------------------------------------------- WTI


def test_the_cl_symbol_follows_yfinances_month_codes():
    assert _wti.cl_symbol(2027, 1) == "CLF27.NYM"
    assert _wti.cl_symbol(2026, 12) == "CLZ26.NYM"


def test_a_contract_s_last_session_is_three_business_days_before_the_25th():
    # December 2026 delivery: 25 November is a Wednesday, a business day.
    assert _wti.last_session(2026, 12, SESSIONS).isoformat() == "2026-11-20"


def test_four_business_days_when_the_25th_is_not_one():
    # January 2027 delivery: 25 December is a holiday.
    assert _wti.last_session(2027, 1, SESSIONS).isoformat() == "2026-12-21"


def test_the_active_month_switches_at_the_open_of_the_third_to_last_session():
    # Last session 20 Nov; third-to-last 18 Nov; ICE opens 18:00 ET on the 17th.
    switch = _wti.switch_time(2026, 12, SESSIONS)
    assert switch == pd.Timestamp("2026-11-17T18:00:00", tz="America/New_York")
    assert _wti.active_contract(switch - pd.Timedelta(minutes=1), SESSIONS) == (2026, 12)
    assert _wti.active_contract(switch, SESSIONS) == (2027, 1)


def test_the_roll_gap_is_removed_by_pricing_on_the_contract_active_at_the_deadline():
    now = pd.Timestamp("2026-11-10T15:00:00Z")
    before_switch = pd.Timestamp("2026-11-16T20:00:00Z")
    month_end = pd.Timestamp("2026-12-01T04:59:59Z")
    assert _wti.contract_to_price(now, before_switch, SESSIONS) == (2026, 12)
    assert _wti.contract_to_price(now, month_end, SESSIONS) == (2027, 1)


def test_uso_vol_is_read_at_wti_s_moneyness_and_scaled():
    class Smile:
        @staticmethod
        def vol_at(x):
            return 0.40 + x, False          # a skew we can read back

    sigma, clamped = _wti.vol_at_same_moneyness(Smile(), spot_wti=80.0, barrier=88.0,
                                                ratio=1.07)
    assert sigma == pytest.approx((0.40 + math.log(88.0 / 80.0)) * 1.07)
    assert clamped is False


def test_touches_count_in_ice_hours_not_stock_hours():
    times = pd.Series(pd.to_datetime([
        "2026-11-09T08:00:00",     # Monday 03:00 ET: ICE open, NYSE closed
        "2026-11-09T22:30:00",     # Monday 17:30 ET: the daily break
        "2026-11-14T15:00:00",     # Saturday
        "2026-11-15T23:30:00",     # Sunday 18:30 ET: open
    ], utc=True))
    assert list(_wti.in_ice_hours(times)) == [True, False, False, True]


def test_without_the_vol_ratio_every_wti_market_is_skipped_and_counted(monkeypatch):
    bet_type = run.discover_types()["poly-hit-monthly-wti"]()
    books = pd.DataFrame([{"market_id": "w1", "ticker": "WTI", "period": "monthly",
                           "contract_type": "hit_up", "barrier": 90.0,
                           "end_date": pd.Timestamp("2026-12-01T04:59:59Z"),
                           "fetched_at_utc": pd.Timestamp("2026-11-10T15:00:00Z"),
                           "outcome_index": 0, "side": "ask", "depth_rank": 0,
                           "price": 0.2, "size": 10.0}])
    monkeypatch.setattr(data, "books", lambda cycle_id, types: books)
    monkeypatch.setattr(_wti, "VOL_RATIO", None)
    assert bet_type.priced("c").empty
    assert bet_type.priced("c2").empty
    assert list(bet_type.skipped.values()) == [1]
