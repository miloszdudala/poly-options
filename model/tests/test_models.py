"""The models of MODELS section 5, on synthetic states.

Each test pins one requirement of the spec, not the model's output in general.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import sim
from sim import NO, YES, Params
from models import DirectionBalanced, EdgeTaker, ScenarioHedged
from test_sim import CYCLE_A, book, fair_rows, state


def market(market_id, ticker, contract_type, *, yes_ask=0.20, yes_size=10_000.0,
           no_ask=0.75, no_size=10_000.0, barrier=200.0):
    return pd.concat([
        book(CYCLE_A, [(yes_ask, yes_size)], market_id=market_id, ticker=ticker,
             contract_type=contract_type, barrier=barrier, outcome_index=YES),
        book(CYCLE_A, [(no_ask, no_size)], market_id=market_id, ticker=ticker,
             contract_type=contract_type, barrier=barrier, outcome_index=NO),
    ], ignore_index=True)


def priced(market_id, ticker, contract_type, *, fair=0.40, delta=0.01, spot=200.0,
           barrier=200.0, **extra):
    return {"market_id": market_id, "ticker": ticker, "contract_type": contract_type,
            "fair_K": fair, "fair_atm": fair, "fair_cont": fair + 0.02,
            "delta": delta, "spot": spot, "barrier": barrier, **extra}


def world(specs, params=None, betas=None, spy_intraday=0.0, portfolio=None):
    """`specs` is a list of (book frame, fair row dict)."""
    books = pd.concat([b for b, _ in specs], ignore_index=True)
    fair = fair_rows(CYCLE_A, [f for _, f in specs])
    params = params or Params()
    return state(books=books, fair=fair, params=params,
                 portfolio=portfolio if portfolio is not None else sim.Portfolio(params),
                 betas=betas or {t: 1.0 for t in fair["ticker"].unique()},
                 spy_intraday=spy_intraday)


# ------------------------------------------------------------------ model A


def test_a_takes_everything_above_the_bar():
    """MODELS 5.1: no portfolio constraint at all. Ten markets on one ticker,
    all in one direction, all clearing the bar - A proposes all ten."""
    specs = [(market(f"m{i}", "NVDA", "hit_up", yes_ask=0.20),
              priced(f"m{i}", "NVDA", "hit_up", fair=0.40)) for i in range(10)]
    orders = EdgeTaker().propose(world(specs))
    assert len({o.market_id for o in orders}) == 10
    assert all(o.outcome_index == YES for o in orders)


def test_a_lifts_the_portfolio_caps_but_not_the_per_market_one():
    """5.1 says "size to the per-market cap" and "no portfolio constraint at
    all"; those are different sentences about different caps."""
    assert EdgeTaker.params.ticker_cap_frac == 1.0
    assert EdgeTaker.params.direction_cap_frac == 1.0
    assert EdgeTaker.params.cap_yes_usd == Params().cap_yes_usd


def test_a_still_refuses_a_market_below_the_bar():
    specs = [(market("m1", "NVDA", "hit_up", yes_ask=0.39),
              priced("m1", "NVDA", "hit_up", fair=0.40))]
    assert EdgeTaker().propose(world(specs)) == []


def test_a_orders_by_roi_so_the_cash_runs_out_on_the_worst_edge():
    specs = [(market("thin", "NVDA", "hit_up", yes_ask=0.35),
              priced("thin", "NVDA", "hit_up", fair=0.40)),
             (market("fat", "AAPL", "hit_up", yes_ask=0.10),
              priced("fat", "AAPL", "hit_up", fair=0.40))]
    orders = EdgeTaker().propose(world(specs))
    assert orders[0].market_id == "fat"


# ------------------------------------------------ P6: the fair each model uses


def test_a_records_the_conservative_fair_it_screened_on():
    """Not `fair_K` alone: MODELS 5.1's bar is `min(fair_K, fair_atm)` and that
    is the number the position was taken against."""
    specs = [(market("m1", "NVDA", "hit_up", yes_ask=0.20),
              priced("m1", "NVDA", "hit_up", fair=0.40))]
    st = world(specs)
    st.fair.loc[0, "fair_atm"] = 0.52
    orders = EdgeTaker().propose(st)
    sim.execute(st, orders)
    position = st.portfolio.open[("m1", YES)]
    assert position.entry_fair == pytest.approx(0.40)
    assert position.fair_source == "min(fair_K,fair_atm)"


def test_a_no_position_records_the_other_end_of_the_two_vols():
    specs = [(market("m1", "NVDA", "hit_up", yes_ask=0.20, no_ask=0.30),
              priced("m1", "NVDA", "hit_up", fair=0.40))]
    st = world(specs)
    st.fair.loc[0, "fair_atm"] = 0.52
    orders = [o for o in EdgeTaker().propose(st) if o.outcome_index == NO]
    assert orders, "the NO side should clear the bar at 30c against a 0.52 fair"
    sim.execute(st, orders)
    position = st.portfolio.open[("m1", NO)]
    assert position.entry_fair == pytest.approx(0.52)
    assert position.fair_source == "max(fair_K,fair_atm)"


# ------------------------------------------ behaviour checks, 2026-09-18


def _held(portfolio, market_id, outcome, contract_type="hit_up", cost=10.0, ticker="ZZ"):
    portfolio.open[(market_id, outcome)] = sim.Position(
        market_id=market_id, outcome_index=outcome, ticker=ticker,
        contract_type=contract_type, barrier=1.0, deadline=pd.NaT, period="w",
        entry_cycle_id=CYCLE_A, entry_at=pd.Timestamp("2026-09-16T14:00:00Z"),
        entry_fair=0.4, shares=1.0, cost_usd=cost)


def test_a_projection_refuses_what_the_fill_engine_would_refuse():
    """`project` is only useful if it agrees with `execute`. A market already
    held on either side fills nothing (MODELS 4.2), so it must project nothing."""
    specs = [(market("m1", "NVDA", "hit_up"), priced("m1", "NVDA", "hit_up"))]
    fresh = world(specs, params=Params())
    order = sim.Order(market_id="m1", outcome_index=YES, max_usd=150.0, limit_price=0.9,
                      reason="t", fair=0.4, fair_source="t")
    assert sim.project(fresh, order)["cost_usd"] > 0

    portfolio = sim.Portfolio(Params())
    _held(portfolio, "m1", YES)
    held = world(specs, params=Params(), portfolio=portfolio)
    same = sim.project(held, order)
    assert same["cost_usd"] == 0 and same["blocker"] == "no_averaging_down"
    other = sim.project(held, sim.Order(market_id="m1", outcome_index=NO, max_usd=500.0,
                                        limit_price=0.99, reason="t", fair=0.4, fair_source="t"))
    assert other["cost_usd"] == 0 and other["blocker"] == "already_other_side"


# ------------------------------------------------------- direction_balanced


def _hold_dir(portfolio, key, contract_type, outcome, cost):
    portfolio.open[(key, outcome)] = sim.Position(
        market_id=key, outcome_index=outcome, ticker="ZZ", contract_type=contract_type,
        barrier=1.0, deadline=pd.NaT, period="w", entry_cycle_id=CYCLE_A,
        entry_at=pd.Timestamp("2026-09-16T14:00:00Z"), entry_fair=0.4, shares=1.0,
        cost_usd=cost)


def test_direction_balanced_stops_one_direction_at_the_slack():
    """Five hit_up YES are all "stock goes up"; with nothing on the down side
    the slack is the whole allowance."""
    specs = [(market(f"u{i}", f"T{i}", "hit_up", yes_ask=0.20),
              priced(f"u{i}", f"T{i}", "hit_up", fair=0.40)) for i in range(5)]
    model = DirectionBalanced(balance_slack=500.0)
    orders = model.propose(world(specs, params=DirectionBalanced.params))
    assert sum(o.max_usd for o in orders) == pytest.approx(500.0)
    assert model.refusals.get("direction_balance") == 1


def test_direction_balanced_counts_a_hit_down_no_as_stock_up():
    """NO on hit_down is a bet the stock will not fall: the same side as YES
    on hit_up, so it uses up the same allowance."""
    portfolio = sim.Portfolio(DirectionBalanced.params)
    _hold_dir(portfolio, "d0", "hit_down", NO, 500.0)
    specs = [(market("u0", "T0", "hit_up", yes_ask=0.20),
              priced("u0", "T0", "hit_up", fair=0.40))]
    orders = DirectionBalanced(balance_slack=500.0).propose(
        world(specs, params=DirectionBalanced.params, portfolio=portfolio))
    assert orders == []


def test_direction_balanced_opens_up_once_the_other_side_is_hold_dir():
    """$400 of "stock goes down" (a hit_up NO) lets "up" grow to $900."""
    portfolio = sim.Portfolio(DirectionBalanced.params)
    _hold_dir(portfolio, "n0", "hit_up", NO, 400.0)
    specs = [(market(f"u{i}", f"T{i}", "hit_up", yes_ask=0.20),
              priced(f"u{i}", f"T{i}", "hit_up", fair=0.40)) for i in range(8)]
    orders = DirectionBalanced(balance_slack=500.0).propose(
        world(specs, params=DirectionBalanced.params, portfolio=portfolio))
    assert sum(o.max_usd for o in orders) == pytest.approx(900.0)


def test_direction_balanced_buys_the_light_side_below_the_bar():
    """$600 of "down" and nothing "up": a hit_up YES with 1.2c of EV misses
    the 3c bar but comes in as a hedge, at most up to the $600 gap."""
    portfolio = sim.Portfolio(DirectionBalanced.params)
    _hold_dir(portfolio, "n0", "hit_up", NO, 600.0)
    specs = [(market("u0", "T0", "hit_up", yes_ask=0.20),
              priced("u0", "T0", "hit_up", fair=0.24))]
    orders = DirectionBalanced().propose(
        world(specs, params=DirectionBalanced.params, portfolio=portfolio))
    assert [o.reason for o in orders] == ["direction_balanced_hedge"]
    assert orders[0].max_usd <= 600.0
    none = DirectionBalanced(hedge_min_ev=None).propose(
        world(specs, params=DirectionBalanced.params, portfolio=portfolio))
    assert none == []


# --------------------------------------------------------- scenario_hedged


def test_scenarios_reproduce_the_model_probability_at_the_centre():
    """k = 1, mu = 0 is the scenario where the options were right."""
    from models.scenario_hedged import K, MU, touch_probs
    centre = int(np.argmin(np.abs(K - 1.0) + np.abs(MU)))
    for q in (0.05, 0.3, 0.7):
        assert touch_probs(q, up=True)[centre] == pytest.approx(q, abs=1e-9)


def test_a_calm_week_pays_no_on_both_sides():
    """Low vol (k < 1) lowers the touch probability above and below the price:
    NO on hit_up and NO on hit_down win together, which is why they do not
    hedge each other."""
    from models.scenario_hedged import K, MU, win_probs
    calm = int(np.argmin(np.abs(K - K.min()) + np.abs(MU)))
    up_no = win_probs(0.4, "hit_up", "NO")
    down_no = win_probs(0.4, "hit_down", "NO")
    assert up_no[calm] > 0.6 + 0.05 and down_no[calm] > 0.6 + 0.05


def test_drift_moves_up_and_down_barriers_opposite_ways():
    from models.scenario_hedged import K, MU, touch_probs
    rally = int(np.argmin(np.abs(K - 1.0) + np.abs(MU - MU.max())))
    assert touch_probs(0.3, up=True)[rally] > 0.3 > touch_probs(0.3, up=False)[rally]


def test_same_ticker_bets_count_as_one_coin_flip():
    """Two $50 NO on the same ticker and side are one bet: their spread adds,
    on two tickers it adds in quadrature."""
    from models.scenario_hedged import Book, win_probs
    w = win_probs(0.5, "hit_up", "NO") * 0          # no scenario spread: coin flips only
    one = Book().plus(("A", "hit_up", 1), 100, 50, w, 0.5).plus(("A", "hit_up", 1), 100, 50, w, 0.5)
    two = Book().plus(("A", "hit_up", 1), 100, 50, w, 0.5).plus(("B", "hit_up", 1), 100, 50, w, 0.5)
    assert one.sd() == pytest.approx(100.0) and two.sd() == pytest.approx(100.0 / math.sqrt(2))


def test_scenario_hedged_trims_a_bet_to_the_sd_limit():
    """$100 of NO at 25c with a 50/50 model probability: sd $200, so a $50 limit
    lets a quarter of it in."""
    from models.scenario_hedged import Book
    model = ScenarioHedged(loss_budget=1e9, sd_budget=50.0, sd_frac=0.0)
    wins = np.full(49, 0.5)
    t = model._largest_fraction(Book(), ("A", "hit_up", 1), 400.0, 100.0, wins, 0.5)
    assert t == pytest.approx(0.25, rel=1e-6)


def test_scenario_hedged_trims_a_bet_to_the_loss_budget():
    """A bet worth nothing in one scenario loses its cost there."""
    from models.scenario_hedged import Book
    model = ScenarioHedged(loss_budget=100.0, loss_frac=0.0, sd_frac=None)
    wins = np.ones(49); wins[0] = 0.0
    t = model._largest_fraction(Book(), ("A", "hit_up", 0), 1_000.0, 150.0, wins, 0.9)
    assert t == pytest.approx(100.0 / 150.0)
    assert model.budget(0.0) == 100.0


def test_scenario_hedged_lets_a_hedge_in_when_it_covers_the_hole():
    """A book losing $900 in scenario 0 takes a bet that pays there."""
    from models.scenario_hedged import Book
    model = ScenarioHedged(loss_budget=1_000.0, loss_frac=0.0, sd_frac=None)
    pnl = np.full(49, 50.0); pnl[0] = -900.0
    book = Book(pnl, {}, 0.0)
    pays_there = np.zeros(49); pays_there[0] = 1.0
    assert model._largest_fraction(book, ("B", "hit_down", 0), 240.0, 40.0, pays_there, 0.2) == 1.0
    loses_there = np.ones(49); loses_there[0] = 0.0
    t = model._largest_fraction(book, ("C", "hit_up", 1), 210.0, 200.0, loses_there, 0.95)
    assert t == pytest.approx(0.5)


def test_scenario_weights_are_a_distribution():
    from models.scenario_hedged import WEIGHTS, K, MU
    assert WEIGHTS.sum() == pytest.approx(1.0) and len(WEIGHTS) == len(K) == len(MU) == 49
    assert K[WEIGHTS.argmax()] == pytest.approx(1.0) and MU[WEIGHTS.argmax()] == pytest.approx(0.0)


# ------------------------------------------------------- the YES risk premium


def test_a_yes_must_clear_the_bar_plus_the_risk_premium():
    """INSIGHTS 7.2: a YES at 20c with fair 0.24 has 4c of raw EV (minus a
    0.8c fee), which clears a 3c bar only without the 2c premium."""
    specs = [(market("u0", "T0", "hit_up", yes_ask=0.20),
              priced("u0", "T0", "hit_up", fair=0.24))]
    state = world(specs)
    assert sim.YES_RISK_PREMIUM == 0.02
    with_premium = sim.candidate_edges(state)
    without = sim.candidate_edges(state, yes_premium=0.0)
    assert with_premium[with_premium["outcome_index"] == YES].empty
    assert not without[without["outcome_index"] == YES].empty


def test_the_premium_is_not_charged_to_no():
    specs = [(market("u0", "T0", "hit_up", yes_ask=0.20),
              priced("u0", "T0", "hit_up", fair=0.05))]
    state = world(specs)
    a = sim.candidate_edges(state)
    b = sim.candidate_edges(state, yes_premium=0.0)
    a, b = a[a["outcome_index"] == NO], b[b["outcome_index"] == NO]
    assert len(a) == len(b) and np.allclose(a["ev"], b["ev"])
