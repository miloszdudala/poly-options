"""The four models of MODELS section 5, on synthetic states.

Each test pins one requirement of the spec, not the model's output in general:
what A refuses to refuse, what B refuses and then accepts, what the quadrant
rule does when a quadrant is empty, and the two rules C is not allowed to bend.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sim
from sim import NO, YES, Params
from models import (BalancedBook, DirectionBalanced, EdgeTaker, EmpiricalLongshot, FactorNeutral,
                    ScenarioHedged)
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


# ------------------------------------------------------------------ model B


def straddle_world(delta=0.01, betas=None, spot=200.0):
    specs = [
        (market("up", "NVDA", "hit_up", yes_ask=0.20),
         priced("up", "NVDA", "hit_up", fair=0.40, delta=delta, spot=spot)),
        (market("down", "NVDA", "hit_down", yes_ask=0.20),
         priced("down", "NVDA", "hit_down", fair=0.40, delta=-delta, spot=spot)),
    ]
    return world(specs, params=FactorNeutral.params, betas=betas or {"NVDA": 1.0})


def test_b_trims_a_position_down_to_the_beta_band():
    """A lone high-delta candidate: the $150 the cap allows would carry $18,000
    of beta-dollar-delta, so B takes the $4.16 that fits the $500 band and
    leaves the rest. Sizing on premium alone would have taken all of it."""
    model = FactorNeutral()
    specs = [(market("up", "NVDA", "hit_up", yes_ask=0.20),
              priced("up", "NVDA", "hit_up", fair=0.40, delta=0.05, spot=500.0))]
    st = world(specs, params=FactorNeutral.params)
    orders = model.propose(st)
    assert len(orders) == 1
    assert orders[0].max_usd < st.params.cap_yes_usd / 10
    exposure = model._beta_delta(sim.candidate_edges(st).iloc[0],
                                 sim.project(st, orders[0])["shares"], 1.0)
    assert abs(exposure) <= model.beta_band_frac * st.params.bankroll + 1e-6


def test_b_refuses_outright_when_the_trim_falls_under_the_venue_minimum():
    """A delta so large that the band allows less than one dollar of premium.
    There is no position to take, and pretending otherwise would put a
    $0.30 fill in the ledger."""
    model = FactorNeutral()
    specs = [(market("up", "NVDA", "hit_up", yes_ask=0.20),
              priced("up", "NVDA", "hit_up", fair=0.40, delta=0.9, spot=5_000.0))]
    st = world(specs, params=FactorNeutral.params)
    assert model.propose(st) == []
    assert sum(model.refusals.values()) == 1


def test_b_refuses_a_candidate_that_would_push_an_already_full_band_further():
    """Nothing to trim towards: the book is already at the band and the
    candidate leans the same way."""
    model = FactorNeutral()
    params = FactorNeutral.params
    portfolio = sim.Portfolio(params)
    portfolio.open[("held", YES)] = sim.Position(
        market_id="held", outcome_index=YES, ticker="AAPL", contract_type="hit_up",
        barrier=1.0, deadline=pd.NaT, period="w", entry_cycle_id=CYCLE_A,
        entry_at=pd.Timestamp("2026-09-16T14:00:00Z"), entry_fair=0.4,
        shares=500.0, cost_usd=100.0)
    specs = [(market("up", "NVDA", "hit_up", yes_ask=0.20),
              priced("up", "NVDA", "hit_up", fair=0.40, delta=0.05, spot=500.0))]
    st = world(specs, params=params, portfolio=portfolio)
    st.fair = pd.concat([st.fair, fair_rows(CYCLE_A, [priced("held", "AAPL", "hit_up",
                                                             delta=0.02, spot=500.0)])],
                        ignore_index=True)
    assert model.propose(st) == []
    assert model.refusals.get("beta_band") == 1


def test_b_accepts_the_same_position_once_an_opposite_one_exists():
    """The pair preference of MODELS 5.2 exists for exactly this: alone, the
    `hit_up` leg breaks the band; with its `hit_down` twin it is delta-flat and
    both are taken."""
    model = FactorNeutral()
    alone = world([(market("up", "NVDA", "hit_up", yes_ask=0.20),
                    priced("up", "NVDA", "hit_up", fair=0.40, delta=0.05, spot=500.0))],
                  params=FactorNeutral.params)
    solo = model.propose(alone)

    paired = straddle_world(delta=0.05, spot=500.0)
    both = FactorNeutral().propose(paired)
    assert {o.market_id for o in both} == {"up", "down"}
    assert all(o.reason == "straddle_pair" for o in both)
    assert len(both) > len(solo)


def test_b_takes_a_straddle_pair_all_or_nothing():
    model = FactorNeutral()
    orders = model.propose(straddle_world())
    markets = [o.market_id for o in orders if o.reason == "straddle_pair"]
    assert sorted(markets) == ["down", "up"]


def test_b_uses_tighter_caps_than_MODELS_4_4():
    assert FactorNeutral.params.direction_cap_frac == 0.25
    assert FactorNeutral.params.ticker_cap_frac == 0.08


def test_b_counts_a_no_position_with_the_opposite_delta_sign():
    model = FactorNeutral()
    row = sim.candidate_edges(straddle_world()).iloc[0]
    yes_side = model._beta_delta(row, 100.0, 2.0)
    flipped = row.copy()
    flipped["outcome_index"] = NO
    assert model._beta_delta(flipped, 100.0, 2.0) == pytest.approx(-yes_side)


def test_b_refuses_a_book_that_breaks_the_variance_budget():
    """MODELS 5.2's budget is `(2% of bankroll)^2` per day, and same-ticker
    positions are fully correlated inside it."""
    model = FactorNeutral()
    one = [{"q": 0.5, "cost": 150.0, "ticker": "NVDA", "direction": sim.LONG_STOCK}]
    budget = (0.02 * 10_000.0) ** 2
    assert model._variance(one) < budget
    many = one * 6
    assert model._variance(many) > budget


def test_the_variance_of_two_opposite_direction_positions_is_just_the_sum():
    model = FactorNeutral()
    a = {"q": 0.5, "cost": 100.0, "ticker": "NVDA", "direction": sim.LONG_STOCK}
    b = {"q": 0.5, "cost": 100.0, "ticker": "AAPL", "direction": sim.SHORT_STOCK}
    assert model._variance([a, b]) == pytest.approx(model._variance([a]) * 2)


def test_same_direction_positions_across_tickers_carry_half_correlation():
    model = FactorNeutral()
    a = {"q": 0.5, "cost": 100.0, "ticker": "NVDA", "direction": sim.LONG_STOCK}
    b = {"q": 0.5, "cost": 100.0, "ticker": "AAPL", "direction": sim.LONG_STOCK}
    single = model._variance([a])
    assert model._variance([a, b]) == pytest.approx(2 * single + 2 * 0.5 * single)


# ------------------------------------------------------------ B-simple


def quadrant_world(n_up_yes=4, portfolio=None):
    specs = [(market(f"u{i}", f"T{i}", "hit_up", yes_ask=0.20),
              priced(f"u{i}", f"T{i}", "hit_up", fair=0.40)) for i in range(n_up_yes)]
    return world(specs, params=BalancedBook.params, portfolio=portfolio)


def test_balanced_book_refuses_a_fourth_long_position_once_the_slack_is_gone():
    """MODELS 5.2b: a quadrant may exceed the smallest quadrant by at most
    `balance_slack`. With the opposite quadrant empty, the slack is the whole
    allowance, and three $150 positions use $450 of a $500 slack - so the
    fourth gets $50 and a fifth gets nothing."""
    model = BalancedBook(balance_slack=500.0)
    orders = model.propose(quadrant_world(n_up_yes=5))
    assert len(orders) == 4
    assert sum(o.max_usd for o in orders) == pytest.approx(500.0)
    assert orders[-1].max_usd == pytest.approx(50.0)
    assert model.refusals.get("quadrant_balance") == 1


def test_balanced_book_lets_a_quadrant_grow_once_its_opposite_has_filled():
    """The allowance is measured against the *smallest* quadrant, so filling
    one other quadrant does not unlock anything while a third is still empty -
    which is the rule doing its job, not a bug."""
    params = BalancedBook.params
    portfolio = sim.Portfolio(params)
    for quadrant, (contract_type, outcome) in {
            "hit_down_YES": ("hit_down", YES), "hit_down_NO": ("hit_down", NO),
            "hit_up_NO": ("hit_up", NO)}.items():
        portfolio.open[(quadrant, outcome)] = sim.Position(
            market_id=quadrant, outcome_index=outcome, ticker="ZZ",
            contract_type=contract_type, barrier=1.0, deadline=pd.NaT, period="w",
            entry_cycle_id=CYCLE_A, entry_at=pd.Timestamp("2026-09-16T14:00:00Z"),
            entry_fair=0.4, shares=1.0, cost_usd=300.0)
    orders = BalancedBook(balance_slack=500.0).propose(
        quadrant_world(n_up_yes=8, portfolio=portfolio))
    assert sum(o.max_usd for o in orders) == pytest.approx(800.0)


def test_balanced_book_uses_no_deltas_and_no_betas():
    """5.2b is B without the Greeks; if it ever reads one, the comparison the
    two models exist for stops meaning anything."""
    import inspect

    import models.balanced_book as module
    text = inspect.getsource(module)
    body = text.split('"""', 2)[-1]          # skip the docstring, which names them
    assert "delta" not in body and "beta" not in body


def test_balanced_book_keeps_bs_ticker_cap():
    assert BalancedBook.params.ticker_cap_frac == FactorNeutral.params.ticker_cap_frac


# ------------------------------------------------------------------ model C


def longshot_world(yes_ask=0.10, empirical=0.02, fair_K=0.05, contract_type="hit_down",
                   spy_intraday=0.0, no_ask=0.88):
    specs = [(market("m1", "NVDA", contract_type, yes_ask=yes_ask, no_ask=no_ask),
              priced("m1", "NVDA", contract_type, fair=fair_K, empirical=empirical))]
    return world(specs, params=EmpiricalLongshot.params, spy_intraday=spy_intraday)


def test_c_sells_a_longshot_the_history_says_is_too_dear():
    orders = EmpiricalLongshot().propose(longshot_world())
    assert len(orders) == 1
    assert orders[0].outcome_index == NO
    assert orders[0].reason == "sell_longshot"


def test_c_will_not_sell_a_longshot_the_history_agrees_with():
    """The empirical frequency has to be at most half the YES price."""
    assert EmpiricalLongshot().propose(longshot_world(empirical=0.09)) == []


def test_the_option_engine_can_veto_but_never_signal():
    """MODELS 5.3. `fair_K` above the YES ask kills the trade; `fair_K` missing
    does not, because a fifth of the universe has no equity chain at all."""
    assert EmpiricalLongshot().propose(longshot_world(fair_K=0.20)) == []
    vetoless = longshot_world(fair_K=float("nan"))
    assert len(EmpiricalLongshot().propose(vetoless)) == 1


def test_c_never_buys_yes_above_fifty_cents():
    """The 'buy the middle' shape is 15c to 50c and nothing else, whatever the
    history says the edge is."""
    model = EmpiricalLongshot()
    for ask in (0.51, 0.60, 0.80, 0.95):
        orders = model.propose(longshot_world(yes_ask=ask, empirical=0.99,
                                              fair_K=float("nan"), no_ask=0.10))
        assert all(o.outcome_index != YES for o in orders), ask


def test_c_buys_the_middle_when_the_history_beats_the_cost_by_four_cents():
    orders = EmpiricalLongshot().propose(
        longshot_world(yes_ask=0.30, empirical=0.40, fair_K=float("nan")))
    assert [o.reason for o in orders] == ["buy_middle"]
    assert orders[0].outcome_index == YES


def test_c_will_not_buy_the_middle_on_a_three_cent_edge():
    assert EmpiricalLongshot().propose(
        longshot_world(yes_ask=0.30, empirical=0.34, fair_K=float("nan"))) == []


def test_c_stops_adding_hit_down_no_once_spy_is_down_one_and_a_half_percent():
    model = EmpiricalLongshot()
    assert len(model.propose(longshot_world(spy_intraday=-0.010))) == 1
    assert model.propose(longshot_world(spy_intraday=-0.016)) == []


def test_the_spy_stop_does_not_un_fire_when_the_tape_bounces():
    """MODELS 5.3 says 'for the rest of the day'. A stop that lifts on a
    recovery is not a stop, and the recovery is when the next leg starts."""
    model = EmpiricalLongshot()
    model.propose(longshot_world(spy_intraday=-0.020))
    assert model.propose(longshot_world(spy_intraday=-0.001)) == []


def test_the_spy_stop_only_blocks_hit_down_no():
    """A crash touches every `hit_down` longshot at once; a `hit_up` longshot
    is the opposite trade and the rule does not name it."""
    model = EmpiricalLongshot()
    orders = model.propose(longshot_world(contract_type="hit_up", spy_intraday=-0.020))
    assert [o.reason for o in orders] == ["sell_longshot"]


def test_c_carries_MODELS_5_3s_own_caps():
    assert EmpiricalLongshot.params.direction_cap_frac == 0.30
    assert EmpiricalLongshot.params.cap_no_usd == 500.0


def test_c_never_uses_an_option_fair_as_its_own():
    orders = EmpiricalLongshot().propose(longshot_world())
    assert all(o.fair_source == "empirical" for o in orders)


# ------------------------------------------------ P6: the fair each model uses


def test_c_records_the_empirical_fair_it_traded_on_not_the_engines():
    """MODELS 5.3 uses the option engine as a veto only. A position that
    records `fair_K` puts the vetoing number into the reliability table as
    though it were the signal."""
    world_ = longshot_world(yes_ask=0.30, empirical=0.40, fair_K=0.05)
    orders = EmpiricalLongshot().propose(world_)
    sim.execute(world_, orders)
    position = next(iter(world_.portfolio.open.values()))
    assert position.entry_fair == pytest.approx(0.40)
    assert position.fair_source == "empirical"
    assert position.entry_fair_engine == pytest.approx(0.05)


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


def test_c_s_ladder_walk_stops_at_the_shape_s_ceiling():
    """'Buy the middle' is 15c-50c. The top of book at 30c qualifies; the
    harness must not then walk into the 52c level behind it, however large
    the empirical edge says the limit could be."""
    yes = book(CYCLE_A, [(0.30, 10.0), (0.52, 10_000.0)], market_id="m1", ticker="NVDA",
               contract_type="hit_up", outcome_index=YES)
    no = book(CYCLE_A, [(0.60, 10_000.0)], market_id="m1", ticker="NVDA",
              contract_type="hit_up", outcome_index=NO)
    st = world([(pd.concat([yes, no], ignore_index=True),
                 priced("m1", "NVDA", "hit_up", fair=float("nan"), empirical=0.90))],
               params=EmpiricalLongshot.params)
    orders = EmpiricalLongshot().propose(st)
    assert [o.reason for o in orders] == ["buy_middle"]
    fills, _ = sim.execute(st, orders)
    assert fills and all(f.price <= 0.50 for f in fills)


def test_balanced_book_does_not_let_a_projected_fill_raise_the_floor():
    """Three quadrants at $500, `hit_up_YES` empty. A YES candidate would
    fill $150 into the empty quadrant; that projection must not license a
    `hit_up_NO` in the same cycle, because the floor is what the portfolio
    holds, not what this cycle hopes to fill."""
    portfolio = sim.Portfolio(BalancedBook.params)
    _held(portfolio, "q1", NO, "hit_up", cost=500.0)
    _held(portfolio, "q2", YES, "hit_down", cost=500.0)
    _held(portfolio, "q3", NO, "hit_down", cost=500.0)
    specs = [(market("y1", "T1", "hit_up", yes_ask=0.20), priced("y1", "T1", "hit_up", fair=0.40)),
             (market("n1", "T2", "hit_up", no_ask=0.60), priced("n1", "T2", "hit_up", fair=0.10))]
    model = BalancedBook(balance_slack=500.0)
    orders = model.propose(world(specs, params=BalancedBook.params, portfolio=portfolio))
    assert [(o.market_id, o.outcome_index) for o in orders] == [("y1", YES)]
    assert model.refusals.get("quadrant_balance") == 1


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
