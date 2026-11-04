"""The types priced straight from the option chain, on a synthetic flat-vol
chain whose answer is known: a call spread there is N(d2)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import data
import fair
import run

run.discover_types()        # puts model/types on sys.path, for `_chain`
from _chain import prob_above  # noqa: E402

CYCLE = "20260916T150000Z-aaaaaa"          # Wednesday 16 Sep, 11:00 ET
NOW = pd.Timestamp("2026-09-16T15:00:00Z")
FRIDAY = pd.Timestamp("2026-09-18")         # expiry; closes 2026-09-18T20:00Z
SPOT, SIGMA, RATE = 100.0, 0.30, 0.04
YEARS = (pd.Timestamp("2026-09-18T20:00:00Z") - NOW).total_seconds() / (365 * 86400)


def n_d2(level, years=YEARS):
    d2 = (math.log(SPOT / level) + (RATE - 0.5 * SIGMA ** 2) * years) / (SIGMA * math.sqrt(years))
    return float(fair.norm_cdf(d2))


# A fine grid, so the spread's discretisation error (about h^2/6 times the
# density's slope, with h the strike spacing) is far below the tolerance.
STRIKES = np.round(np.arange(80.0, 120.01, 0.1), 1)


def calls(expiry=FRIDAY, strikes=STRIKES, years=YEARS):
    prices = fair.bs_price(SPOT, strikes, years, SIGMA, RATE, True)
    return pd.DataFrame({"underlying": "NVDA", "option_type": "call", "expiry": expiry,
                         "strike": strikes, "mid": prices})


def test_a_call_spread_on_a_flat_vol_chain_is_n_d2():
    for level in (95.0, 100.0, 104.0):           # listed: the spread is centred
        assert prob_above(calls(), level, RATE, YEARS) == pytest.approx(n_d2(level), abs=2e-3)
    assert prob_above(calls(), 102.55, RATE, YEARS) == pytest.approx(n_d2(102.55), abs=2e-3)


def test_a_level_outside_the_listed_strikes_has_no_price():
    assert math.isnan(prob_above(calls(), 130.0, RATE, YEARS))


# ------------------------------------------------------------- the types


def wire(monkeypatch, bet_type, contract_type, period, terms, end_date, chain=None):
    """One market `m1` on NVDA, its books, its terms and the chain, in memory."""
    books = pd.DataFrame([{"cycle_id": CYCLE, "fetched_at_utc": NOW, "market_id": "m1",
                           "ticker": "NVDA", "contract_type": contract_type,
                           "end_date": pd.Timestamp(end_date, tz="UTC"), "period": period,
                           "outcome_index": i, "side": "ask", "depth_rank": 0,
                           "price": 0.5, "size": 100.0} for i in (0, 1)])
    monkeypatch.setattr(data, "books", lambda cycle_id, types: books)
    monkeypatch.setattr(data, "option_chain", lambda cycle_id, symbols: (
        calls() if chain is None else chain))
    monkeypatch.setattr(data, "quote_times", lambda cycle_id: pd.Series(
        {"NVDA": NOW}, dtype="object"))
    monkeypatch.setattr(data, "risk_free_rate", lambda when: RATE)
    bet_type._terms = pd.DataFrame([{"market_id": "m1", "ticker": "NVDA",
                                     "contract_type": contract_type, **terms}]).set_index("market_id")
    return bet_type


def priced(monkeypatch, name, contract_type, period, terms, end_date, chain=None):
    bet_type = run.discover_types()[name]()
    wire(monkeypatch, bet_type, contract_type, period, terms, end_date, chain)
    return bet_type, bet_type.priced(CYCLE)


FRIDAY_END = "2026-09-18T23:59:59"


def test_close_above_weekly_is_the_call_spread(monkeypatch):
    _, out = priced(monkeypatch, "poly-close-above-weekly-stock", "close_above", "weekly",
                    {"strike": 104.0, "direction": "above"}, FRIDAY_END)
    row = out.iloc[0]
    assert row["fair"] == pytest.approx(n_d2(104.0), abs=2e-3)
    assert row["yes_direction"] == "up" and row["yes_vol"] is None


def test_close_below_is_the_complement(monkeypatch):
    _, out = priced(monkeypatch, "poly-close-above-weekly-stock", "close_above", "weekly",
                    {"strike": 96.0, "direction": "below"}, FRIDAY_END)
    assert out.iloc[0]["fair"] == pytest.approx(1 - n_d2(96.0), abs=2e-3)
    assert out.iloc[0]["yes_direction"] == "down"


def test_close_above_daily_uses_the_expiry_that_closes_that_day(monkeypatch):
    years = (pd.Timestamp("2026-09-16T20:00:00Z") - NOW).total_seconds() / (365 * 86400)
    chain = pd.concat([calls(pd.Timestamp("2026-09-16"), years=years), calls()])
    _, out = priced(monkeypatch, "poly-close-above-daily-stock", "close_above", "daily",
                    {"strike": 101.0, "direction": "above"}, "2026-09-16T23:59:59", chain)
    assert out.iloc[0]["fair"] == pytest.approx(n_d2(101.0, years), abs=2e-3)


def test_a_market_with_no_expiry_at_its_deadline_is_skipped_and_counted(monkeypatch):
    bet_type, out = priced(monkeypatch, "poly-close-above-monthly-stock", "close_above",
                           "monthly", {"strike": 104.0, "direction": "above"},
                           "2026-09-30T23:59:59")
    assert out.empty
    assert bet_type.skipped == {"no expiry at the deadline": 1}


def test_range_is_the_difference_of_two_close_aboves(monkeypatch):
    _, out = priced(monkeypatch, "poly-range-weekly-stock", "range", "weekly",
                    {"range_low": 98.0, "range_high": 102.0}, FRIDAY_END)
    row = out.iloc[0]
    assert row["fair"] == pytest.approx(n_d2(98.0) - n_d2(102.0), abs=3e-3)
    assert row["yes_direction"] is None


def test_an_open_ended_range_leans_one_way(monkeypatch):
    _, out = priced(monkeypatch, "poly-range-weekly-stock", "range", "weekly",
                    {"range_low": float("nan"), "range_high": 102.0}, FRIDAY_END)
    assert out.iloc[0]["fair"] == pytest.approx(1 - n_d2(102.0), abs=2e-3)
    assert out.iloc[0]["yes_direction"] == "down"


def test_up_down_strikes_at_the_days_open(monkeypatch):
    years = (pd.Timestamp("2026-09-16T20:00:00Z") - NOW).total_seconds() / (365 * 86400)
    chain = calls(pd.Timestamp("2026-09-16"), years=years)
    bars = pd.DataFrame({"symbol": "NVDA", "bar_time": [pd.Timestamp("2026-09-16T13:30:00Z")],
                         "open": [99.0]})
    monkeypatch.setattr(data, "session_bars_5m", lambda symbol: bars)
    _, out = priced(monkeypatch, "poly-up-down-daily-stock", "up_down", "daily",
                    {"outcome_0": "Up", "outcome_1": "Down"}, "2026-09-16T23:59:59", chain)
    assert out.iloc[0]["fair"] == pytest.approx(n_d2(99.0, years), abs=2e-3)
    assert out.iloc[0]["yes_direction"] == "up"


def test_an_up_down_market_settles_on_its_label(monkeypatch):
    bet_type = run.discover_types()["poly-up-down-daily-stock"]()
    bet_type._terms = pd.DataFrame([{"market_id": "m1", "outcome_0": "Up", "outcome_1": "Down"},
                                    {"market_id": "m2", "outcome_0": "Up", "outcome_1": "Down"}]
                                   ).set_index("market_id")
    monkeypatch.setattr(data, "resolutions_upto", lambda when: pd.DataFrame(
        {"market_id": ["m1", "m2"], "result": ["Up", "Down"], "outcome_prices": [None, None],
         "settled_at_utc": [NOW, NOW]}))
    assert list(bet_type.resolutions(NOW)["result"]) == ["Yes", "No"]


def test_a_stale_chain_is_no_chain(monkeypatch):
    bet_type = run.discover_types()["poly-close-above-weekly-stock"]()
    wire(monkeypatch, bet_type, "close_above", "weekly", {"strike": 104.0}, FRIDAY_END)
    monkeypatch.setattr(data, "quote_times", lambda cycle_id: pd.Series(
        {"NVDA": NOW - pd.Timedelta(hours=2)}, dtype="object"))
    assert bet_type.priced(CYCLE).empty
    assert bet_type.skipped == {"no fresh option chain": 1}


def test_a_skipped_market_is_counted_once_however_many_cycles_skip_it(monkeypatch):
    bet_type, _ = priced(monkeypatch, "poly-close-above-monthly-stock", "close_above",
                         "monthly", {"strike": 104.0, "direction": "above"},
                         "2026-09-30T23:59:59")
    bet_type._books = None
    bet_type.priced(CYCLE)
    assert bet_type.skipped == {"no expiry at the deadline": 1}
