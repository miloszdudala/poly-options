from dataclasses import replace

import pandas as pd
import pytest
from conftest import book_rows, fair_row

import strategy
import trader


def cands(s, fairs, books):
    return strategy.candidates(pd.DataFrame(fairs), trader.best_asks(pd.DataFrame(books)), s)


def test_yes_pays_the_premium_and_no_does_not(s):
    # fair 0.30: YES at 0.20 has EV 0.30-0.02-0.208 = 0.072; NO at 0.60 has
    # EV 0.70-0.616 = 0.084. Both clear min_ev 0.06.
    books = book_rows(outcome_index=0, asks=[(0.20, 100)]) + \
        book_rows(outcome_index=1, asks=[(0.60, 100)])
    out = cands(s, [fair_row()], books)
    by_side = out.set_index("outcome_index")
    assert by_side.loc[0, "ev"] == pytest.approx(0.30 - 0.02 - (0.20 + 0.04 * 0.20))
    assert by_side.loc[1, "ev"] == pytest.approx(0.70 - (0.60 + 0.04 * 0.40))
    # Without the premium the same YES would look 2c better.
    no_prem = cands(replace(s, yes_premium=0.0), [fair_row()], books).set_index("outcome_index")
    assert no_prem.loc[0, "ev"] - by_side.loc[0, "ev"] == pytest.approx(0.02)


def test_cautious_fair_is_min_for_yes_max_for_no(s):
    books = book_rows(outcome_index=0, asks=[(0.10, 100)]) + \
        book_rows(outcome_index=1, asks=[(0.50, 100)])
    out = cands(s, [fair_row(fair_K=0.25, fair_atm=0.35)], books).set_index("outcome_index")
    assert out.loc[0, "fair"] == 0.25
    assert out.loc[1, "fair"] == 0.35


@pytest.mark.parametrize("flag", ["touch_pending", "deep_otm", "no_chain"])
def test_flagged_markets_are_never_candidates(s, flag):
    out = cands(s, [fair_row(**{flag: True})], book_rows(asks=[(0.05, 100)]))
    assert out.empty


def test_missing_one_vol_is_not_a_candidate(s):
    out = cands(s, [fair_row(fair_atm=float("nan"))], book_rows(asks=[(0.05, 100)]))
    assert out.empty


def test_unrepaired_earnings_gap_is_dropped(s):
    out = cands(s, [fair_row(earnings_gap=True, vol_source="strike")], book_rows(asks=[(0.05, 100)]))
    assert out.empty
    ok = cands(s, [fair_row(earnings_gap=True, vol_source="pre_earnings_flat")],
               book_rows(asks=[(0.05, 100)]))
    assert len(ok) == 1


def test_walk_stops_at_the_limit(s):
    taken = strategy.walk([(0.30, 100), (0.20, 100), (0.40, 100)], limit_cost=0.33,
                          budget_usd=100, s=s)
    assert [t["price"] for t in taken] == [0.20, 0.30]


def test_walk_takes_half_a_level(s):
    taken = strategy.walk([(0.20, 10)], limit_cost=0.5, budget_usd=100, s=s)
    assert taken[0]["shares"] == pytest.approx(5.0)


def test_walk_respects_budget_all_in(s):
    taken = strategy.walk([(0.50, 1000)], limit_cost=0.9, budget_usd=5.0, s=s)
    assert sum(t["cost_usd"] for t in taken) == pytest.approx(5.0)


def test_walk_refuses_below_venue_minimum(s):
    # $5 at 0.9 buys ~5.4 shares: fine at a 5-share minimum, refused at 6.
    assert strategy.walk([(0.90, 1000)], 0.99, 5.0, s, min_shares=5)
    assert strategy.walk([(0.90, 1000)], 0.99, 5.0, s, min_shares=6) == []


def test_walk_refuses_under_a_dollar(s):
    assert strategy.walk([(0.10, 1000)], 0.5, 0.9, s) == []


def test_venue_fee_is_lower_than_conservative(s):
    venue = replace(s, fee_model="venue")
    for p in (0.1, 0.5, 0.9):
        assert strategy.fee_per_share(p, venue) <= strategy.fee_per_share(p, s)
