"""The extensions priced straight from the option chain (markets
`polymarket_chain`, prob `call_spread`), on a synthetic flat-vol chain whose
answer is known: a call spread there is N(d2)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import store
from pipeline import config as configs, maths as fair, settle
from pipeline.core import Cycle, price
from pipeline.prob import prob_above, settled_at_the_close

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
    """A whole chain, calls and puts."""
    return pd.concat([
        pd.DataFrame({"underlying": "NVDA", "option_type": kind, "contract_symbol": "NVDA261016",
                      "expiry": expiry, "strike": strikes,
                      "mid": fair.bs_price(SPOT, strikes, years, SIGMA, RATE, kind == "call")})
        for kind in ("call", "put")], ignore_index=True)


def up_down_bars():
    return pd.DataFrame({"symbol": "NVDA",
                         "bar_time": pd.to_datetime(["2026-09-15T19:55:00Z",
                                                     "2026-09-16T13:30:00Z"], utc=True),
                         "open": [98.5, 200.0], "close": [99.0, 201.0]})


def test_a_call_spread_on_a_flat_vol_chain_is_n_d2():
    for level in (95.0, 100.0, 104.0):           # listed: the spread is centred
        assert prob_above(calls(), level, RATE, YEARS) == pytest.approx(n_d2(level), abs=2e-3)
    assert prob_above(calls(), 102.55, RATE, YEARS) == pytest.approx(n_d2(102.55), abs=2e-3)


def test_a_level_below_the_money_is_read_from_the_puts():
    """A deep in-the-money call spread is noise: wide mids, not the probability."""
    chain = calls()
    noisy = (chain["option_type"] == "call") & (chain["strike"] < 90.0)
    chain.loc[noisy, "mid"] = 99.0 - chain.loc[noisy, "strike"]        # a spread of exactly 1.000
    assert prob_above(chain, 85.0, RATE, YEARS) == pytest.approx(n_d2(85.0), abs=2e-3)


def test_morning_settled_index_options_are_dropped_on_a_third_friday():
    chain = pd.DataFrame({"contract_symbol": ["SPX261016C05000000", "SPXW261016C05000000",
                                              "NDX261016C20000000", "NDXP261016C20000000",
                                              "NVDA261016C00100000"]})
    assert list(settled_at_the_close(chain)["contract_symbol"]) == [
        "SPXW261016C05000000", "NDXP261016C20000000", "NVDA261016C00100000"]


def test_a_level_outside_the_listed_strikes_has_no_price():
    assert math.isnan(prob_above(calls(), 130.0, RATE, YEARS))


# ---------------------------------------------------------- the extensions


FRIDAY_END = "2026-09-18T23:59:59"


def priced(monkeypatch, config_name, contract_type, period, terms, end_date, chain=None,
           quote_age=pd.Timedelta(0), bars=None):
    """Price one market `m1` on NVDA through the config's pricing steps."""
    books = pd.DataFrame([{"cycle_id": CYCLE, "fetched_at_utc": NOW, "market_id": "m1",
                           "ticker": "NVDA", "contract_type": contract_type,
                           "end_date": pd.Timestamp(end_date, tz="UTC"), "period": period,
                           "outcome_index": i, "side": "ask", "depth_rank": 0,
                           "price": 0.5, "size": 100.0} for i in (0, 1)])
    base = {"strike": None, "direction": None, "range_low": None, "range_high": None,
            "outcome_0": "Yes", "outcome_1": "No", "event_slug": "nvda-up-or-down"}
    if bars is not None:
        monkeypatch.setattr(store, "session_bars_5m", lambda symbol: bars)
    monkeypatch.setattr(store, "books", lambda cycle_id, types: books)
    monkeypatch.setattr(store, "market_terms", lambda kind: pd.DataFrame(
        [{"market_id": "m1", **base, **terms}]).set_index("market_id"))
    monkeypatch.setattr(store, "option_chain", lambda cycle_id, symbols: (
        calls() if chain is None else chain))
    monkeypatch.setattr(store, "quote_times", lambda cycle_id: pd.Series(
        {"NVDA": NOW - quote_age}, dtype="object"))
    monkeypatch.setattr(store, "risk_free_rate", lambda when: RATE)
    config = configs.load(config_name)
    return price(Cycle(CYCLE, (contract_type,)), config).iloc[0]


def test_close_above_weekly_is_the_call_spread(monkeypatch):
    row = priced(monkeypatch, "close_above_weekly", "close_above", "weekly",
                 {"strike": 104.0, "direction": "above"}, FRIDAY_END)
    assert row["fair"] == pytest.approx(n_d2(104.0), abs=2e-3)
    assert row["yes_direction"] == "up" and row["yes_vol"] is None and row["tradeable"]


def test_close_below_is_the_complement(monkeypatch):
    row = priced(monkeypatch, "close_above_weekly", "close_above", "weekly",
                 {"strike": 96.0, "direction": "below"}, FRIDAY_END)
    assert row["fair"] == pytest.approx(1 - n_d2(96.0), abs=2e-3)
    assert row["yes_direction"] == "down"


def test_close_above_daily_uses_the_expiry_that_closes_that_day(monkeypatch):
    years = (pd.Timestamp("2026-09-16T20:00:00Z") - NOW).total_seconds() / (365 * 86400)
    chain = pd.concat([calls(pd.Timestamp("2026-09-16"), years=years), calls()])
    row = priced(monkeypatch, "close_above_daily", "close_above", "daily",
                 {"strike": 101.0, "direction": "above"}, "2026-09-16T23:59:59", chain)
    assert row["fair"] == pytest.approx(n_d2(101.0, years), abs=2e-3)


def test_a_market_with_no_expiry_at_its_deadline_is_ruled_out_never_interpolated(monkeypatch):
    row = priced(monkeypatch, "close_above_monthly", "close_above", "monthly",
                 {"strike": 104.0, "direction": "above"}, "2026-09-30T23:59:59")
    assert math.isnan(row["fair"]) and row["gate"] == "no expiry at the deadline"


def test_range_is_the_difference_of_two_close_aboves(monkeypatch):
    row = priced(monkeypatch, "range_weekly", "range", "weekly",
                 {"range_low": 98.0, "range_high": 102.0}, FRIDAY_END)
    assert row["fair"] == pytest.approx(n_d2(98.0) - n_d2(102.0), abs=3e-3)
    assert row["yes_direction"] is None


def test_an_open_ended_range_leans_one_way(monkeypatch):
    row = priced(monkeypatch, "range_weekly", "range", "weekly",
                 {"range_low": float("nan"), "range_high": 102.0}, FRIDAY_END)
    assert row["fair"] == pytest.approx(1 - n_d2(102.0), abs=2e-3)
    assert row["yes_direction"] == "down"


def test_up_down_strikes_at_the_previous_close(monkeypatch):
    """Up if today's close beats the previous trading day's close (98.7% of 760
    resolved markets; today's open 74%)."""
    years = (pd.Timestamp("2026-09-16T20:00:00Z") - NOW).total_seconds() / (365 * 86400)
    chain = calls(pd.Timestamp("2026-09-16"), years=years)
    row = priced(monkeypatch, "up_down_daily", "up_down", "daily",
                 {"outcome_0": "Up", "outcome_1": "Down"}, "2026-09-16T23:59:59", chain,
                 bars=up_down_bars())
    assert row["fair"] == pytest.approx(n_d2(99.0, years), abs=2e-3)
    assert row["yes_direction"] == "up"


def test_an_opens_up_or_down_market_is_ruled_out(monkeypatch):
    years = (pd.Timestamp("2026-09-16T20:00:00Z") - NOW).total_seconds() / (365 * 86400)
    row = priced(monkeypatch, "up_down_daily", "up_down", "daily",
                 {"outcome_0": "Up", "outcome_1": "Down",
                  "event_slug": "spx-opens-up-or-down-on-september-16-2026"},
                 "2026-09-16T23:59:59", calls(pd.Timestamp("2026-09-16"), years=years),
                 bars=up_down_bars())
    assert row["gate"] == "decided at the open, not the close"


def test_a_quote_newer_than_the_book_is_not_priced(monkeypatch):
    row = priced(monkeypatch, "close_above_weekly", "close_above", "weekly",
                 {"strike": 104.0}, FRIDAY_END, quote_age=-pd.Timedelta(minutes=5))
    assert row["gate"] == "option quote newer than the book"


def test_an_up_down_market_settles_on_its_label(monkeypatch):
    monkeypatch.setattr(store, "outcome_labels", lambda: pd.DataFrame(
        {"outcome_0": ["Up", "Up"], "outcome_1": ["Down", "Down"]}, index=["m1", "m2"]))
    monkeypatch.setattr(store, "resolutions_upto", lambda when: pd.DataFrame(
        {"market_id": ["m1", "m2"], "result": ["Up", "Down"], "outcome_prices": [None, None],
         "settled_at_utc": [NOW, NOW]}))
    assert list(settle.resolutions(NOW)["result"]) == ["Yes", "No"]


def test_a_stale_chain_is_no_chain(monkeypatch):
    row = priced(monkeypatch, "close_above_weekly", "close_above", "weekly",
                 {"strike": 104.0}, FRIDAY_END, quote_age=pd.Timedelta(hours=2))
    assert row["gate"] == "no fresh option chain"
