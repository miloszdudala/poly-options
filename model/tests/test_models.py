"""The models of MODELS section 5, on synthetic states.

Each test pins one requirement of the spec, not the model's output in general.
factor_neutral, balanced_book and empirical_longshot were removed on
2026-10-17; their tests went with them (git history has both).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sim
from sim import NO, YES, Params
from models import DirectionBalanced, EdgeTaker, ScenarioHedged
from test_sim import BOOK_COLUMNS, CYCLE_A, CYCLE_B, book, fair_rows, state


def market(market_id, ticker, contract_type, *, yes_ask=0.20, yes_size=10_000.0,
           no_ask=0.75, no_size=10_000.0, barrier=200.0):
    return pd.concat([
        book(CYCLE_A, [(yes_ask, yes_size)], market_id=market_id, ticker=ticker,
             contract_type=contract_type, barrier=barrier, outcome_index=YES),
        book(CYCLE_A, [(no_ask, no_size)], market_id=market_id, ticker=ticker,
             contract_type=contract_type, barrier=barrier, outcome_index=NO),
    ], ignore_index=True)


def priced(market_id, ticker, contract_type, *, fair=0.40, delta=0.01, spot=200.0,
           barrier=200.0, empirical=None, **extra):
    return {"market_id": market_id, "ticker": ticker, "contract_type": contract_type,
            "fair_K": fair, "fair_atm": fair, "fair_cont": fair + 0.02,
            "fair_empirical": fair if empirical is None else empirical,
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


# --------------------------------------------------------- scenario_hedged


def test_scenario_payoffs_follow_the_barrier():
    from models.scenario_hedged import SCENARIOS, payoff
    up_yes = payoff("hit_up", YES, 1.0)
    assert up_yes.tolist() == [float(z >= 1.0) for z in SCENARIOS]
    assert (payoff("hit_up", NO, 1.0) == 1 - up_yes).all()
    assert payoff("hit_down", YES, -1.0)[0] == 1.0 and payoff("hit_down", YES, -1.0)[-1] == 0.0


def test_scenario_hedged_trims_a_bet_to_the_loss_budget():
    """A YES loses its whole cost in every scenario that does not reach it, so
    with a $100 budget and nothing held it can put in $100 and no more."""
    model = ScenarioHedged(loss_budget=100.0)
    t = model._largest_scale(np.zeros(3), np.array([-150.0, -150.0, 300.0]))
    assert t == pytest.approx(100.0 / 150.0)


def test_scenario_hedged_lets_a_hedge_in_when_it_covers_the_hole():
    """A book losing $900 if the market falls takes a bet that pays then."""
    model = ScenarioHedged(loss_budget=1_000.0)
    book = np.array([-900.0, 50.0, 50.0])
    assert model._largest_scale(book, np.array([200.0, -40.0, -40.0])) == 1.0
    assert model._largest_scale(book, np.array([-200.0, 10.0, 10.0])) == pytest.approx(0.5)


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
