"""The harness, on synthetic books: fills, resolution,
the caps, and the rule that a model cannot see the future.

Everything here is built in memory. A test that needs the Parquet store to
decide whether a fee is right is testing the store.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import results
import sim
from sim import YES, NO, LookaheadError, Order, Params

CYCLE_A = "20260916T140000Z-aaaaaa"
CYCLE_B = "20260916T141500Z-bbbbbb"
CYCLE_C = "20260916T143000Z-cccccc"

BOOK_COLUMNS = ["cycle_id", "fetched_at_utc", "book_timestamp_utc", "market_id",
                "token_id", "ticker", "contract_type", "barrier", "strike",
                "end_date", "period", "outcome", "outcome_index", "side",
                "depth_rank", "price", "size"]


def book(cycle_id, levels, *, market_id="m1", ticker="NVDA", contract_type="hit_up",
         barrier=200.0, outcome_index=YES, side="ask", end_date="2026-09-18T20:00:00Z"):
    """`levels` is [(price, size), ...] in depth order."""
    now = pd.Timestamp(f"2026-09-16T{cycle_id[9:11]}:{cycle_id[11:13]}:00Z")
    return pd.DataFrame([{
        "cycle_id": cycle_id, "fetched_at_utc": now, "book_timestamp_utc": now,
        "market_id": market_id, "token_id": f"{market_id}-{outcome_index}",
        "ticker": ticker, "contract_type": contract_type, "barrier": barrier,
        "strike": None, "end_date": pd.Timestamp(end_date), "period": "weekly",
        "outcome": "Yes" if outcome_index == YES else "No",
        "outcome_index": outcome_index, "side": side, "depth_rank": rank,
        "price": price, "size": size,
    } for rank, (price, size) in enumerate(levels)], columns=BOOK_COLUMNS)


def fair_rows(cycle_id, rows):
    base = {"cycle_id": cycle_id, "market_id": "m1", "ticker": "NVDA",
            "contract_type": "hit_up", "barrier": 200.0, "period": "weekly",
            "fair": 0.40,
            "delta": 0.01, "spot": 190.0,
            "deep_otm": False, "no_chain": False, "touch_pending": False,
            "stale_book": False,
            "deadline_utc": pd.Timestamp("2026-09-18T20:00:00Z")}
    return pd.DataFrame([{**base, **row} for row in rows])


def state(cycle_id=CYCLE_A, books=None, fair=None, portfolio=None, params=None,
          betas=None, spy_intraday=0.0, loader=None, resolutions=None):
    params = params or Params()
    portfolio = portfolio if portfolio is not None else sim.Portfolio(params)
    return sim.CycleState(
        cycle_id=cycle_id, now=pd.Timestamp("2026-09-16T14:00:00Z"),
        books=books if books is not None else book(cycle_id, [(0.30, 100.0)]),
        fair=fair if fair is not None else fair_rows(cycle_id, [{}]),
        resolutions=resolutions if resolutions is not None else pd.DataFrame(),
        portfolio=portfolio, betas=betas or {"NVDA": 1.9},
        spy_intraday=spy_intraday, params=params, loader=loader)


# ------------------------------------------------------------ the arithmetic


def test_the_fee_is_four_percent_of_the_cheaper_side():
    assert sim.fee_per_share(0.30) == pytest.approx(0.012)
    assert sim.fee_per_share(0.95) == pytest.approx(0.002)
    assert sim.fee_per_share(0.50) == pytest.approx(0.02)


def test_all_in_cost_adds_the_fee_once():
    assert sim.all_in_cost(0.30) == pytest.approx(0.312)


@pytest.mark.parametrize("contract_type,outcome,expected", [
    ("hit_up", YES, sim.LONG_STOCK),
    ("hit_down", NO, sim.LONG_STOCK),
    ("hit_down", YES, sim.SHORT_STOCK),
    ("hit_up", NO, sim.SHORT_STOCK),
])
def test_direction_is_the_stock_axis_not_the_yes_no_axis(contract_type, outcome, expected):
    """MODELS 5.2b's table: a book of YES `hit_up` and NO `hit_down` is
    'balanced' on YES/NO and entirely long the market."""
    assert sim.direction_of(contract_type, outcome) == expected


def test_the_four_quadrants_are_named_as_MODELS_5_2b_names_them():
    assert sim.quadrant_of("hit_up", YES) == "hit_up_YES"
    assert sim.quadrant_of("hit_down", NO) == "hit_down_NO"


# -------------------------------------------------------------- the ladder


def order(**kw):
    base = {"market_id": "m1", "outcome_index": YES, "max_usd": 1000.0,
            "limit_price": 0.50, "reason": "test"}
    return Order(**{**base, **kw})


def test_the_walk_takes_half_of_each_level_and_no_more():
    levels = book(CYCLE_A, [(0.30, 100.0), (0.31, 100.0)])
    taken = sim.walk_ladder(levels, order(), 1000.0, Params())
    assert [t["shares"] for t in taken] == [50.0, 50.0]


def test_the_walk_stops_at_the_first_level_that_breaches_the_limit():
    """MODELS 4.2. 0.40 all-in is 0.416, which is past a 0.41 limit; the 0.30
    level in front of it still fills, and nothing behind it is considered."""
    levels = book(CYCLE_A, [(0.30, 100.0), (0.40, 100.0), (0.20, 100.0)])
    taken = sim.walk_ladder(levels, order(limit_price=0.41), 1000.0, Params())
    assert [t["depth_rank"] for t in taken] == [0]


def test_the_walk_stops_when_the_budget_runs_out_mid_level():
    levels = book(CYCLE_A, [(0.30, 1000.0)])
    taken = sim.walk_ladder(levels, order(), 31.20, Params())
    assert taken[0]["shares"] == pytest.approx(100.0)
    assert taken[0]["cost_usd"] == pytest.approx(31.20)


def test_a_fill_under_the_venue_minimum_is_not_a_fill():
    """A $0.40 sweep is an accounting entry, not a trade. The whole walk goes,
    not just its tail: an exchange rejects the order, it does not shrink it."""
    levels = book(CYCLE_A, [(0.30, 1.0)])
    assert sim.walk_ladder(levels, order(), 1000.0, Params()) == []


def test_the_fee_charged_matches_the_price_paid_on_every_level():
    levels = book(CYCLE_A, [(0.30, 100.0), (0.94, 100.0)])
    taken = sim.walk_ladder(levels, order(limit_price=0.99), 10_000.0, Params())
    assert taken[0]["fee_usd"] == pytest.approx(0.04 * 0.30 * 50)
    assert taken[1]["fee_usd"] == pytest.approx(0.04 * 0.06 * 50)


# ----------------------------------------------------------------- the caps


def test_the_per_market_cap_binds_and_is_named():
    params = Params()
    grant = sim.budget_for(order(max_usd=10_000.0), sim.Portfolio(params), params,
                           "hit_up", "NVDA")
    assert grant.wanted_usd == params.cap_yes_usd
    assert grant.budget_usd == pytest.approx(params.cap_yes_usd)


def test_the_no_cap_is_the_larger_one_because_a_no_costs_more_per_share():
    params = Params()
    grant = sim.budget_for(order(outcome_index=NO, max_usd=10_000.0),
                           sim.Portfolio(params), params, "hit_up", "NVDA")
    assert grant.wanted_usd == params.cap_no_usd


def test_the_ticker_cap_is_a_share_of_bankroll_and_counts_every_market():
    params = Params(bankroll=1_000.0, ticker_cap_frac=0.10, cap_yes_usd=500.0)
    portfolio = sim.Portfolio(params)
    portfolio.open[("other", YES)] = sim.Position(
        market_id="other", outcome_index=YES, ticker="NVDA", contract_type="hit_up",
        barrier=1.0, deadline=pd.NaT, period="weekly", entry_cycle_id=CYCLE_A,
        entry_at=pd.Timestamp("2026-09-16T13:40:00Z"), entry_fair=0.4, cost_usd=80.0)
    grant = sim.budget_for(order(max_usd=500.0), portfolio, params, "hit_up", "NVDA")
    assert grant.budget_usd == pytest.approx(20.0)
    assert grant.blocker == "ticker_cap"


def test_the_direction_cap_pools_hit_up_yes_with_hit_down_no():
    params = Params(bankroll=1_000.0, direction_cap_frac=0.40, cap_yes_usd=500.0,
                    ticker_cap_frac=1.0)
    portfolio = sim.Portfolio(params)
    portfolio.open[("other", NO)] = sim.Position(
        market_id="other", outcome_index=NO, ticker="AAPL", contract_type="hit_down",
        barrier=1.0, deadline=pd.NaT, period="weekly", entry_cycle_id=CYCLE_A,
        entry_at=pd.Timestamp("2026-09-16T13:40:00Z"), entry_fair=0.4, cost_usd=350.0)
    grant = sim.budget_for(order(max_usd=500.0), portfolio, params, "hit_up", "NVDA")
    assert grant.budget_usd == pytest.approx(50.0)
    assert grant.blocker == "direction_cap"


def test_execute_stops_at_the_per_market_cap_however_deep_the_book():
    params = Params(cap_yes_usd=25.0, min_fill_usd=1.0)
    st = state(books=book(CYCLE_A, [(0.30, 10_000.0)]), params=params)
    fills, capacity = sim.execute(st, [order(max_usd=10_000.0)])
    assert sum(f.cost_usd for f in fills) == pytest.approx(25.0)
    assert capacity[0]["wanted_usd"] == 25.0


def test_a_model_cannot_buy_both_sides_of_one_market():
    """MODELS 4.2: one market, one direction, per model, per lifetime."""
    st = state(books=pd.concat([book(CYCLE_A, [(0.30, 1000.0)], outcome_index=YES),
                                book(CYCLE_A, [(0.60, 1000.0)], outcome_index=NO)]))
    sim.execute(st, [order()])
    _, capacity = sim.execute(st, [order(outcome_index=NO, limit_price=0.90)])
    assert capacity[0]["blocker"] == "already_other_side"


def test_one_market_one_direction_per_lifetime_by_default():
    """MODELS 4.2. No model in section 5 asks to re-enter, so a repeated order
    on a market already held is refused however far the fair has moved."""
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]), params=Params(cap_yes_usd=20.0))
    sim.execute(st, [order()])
    _, capacity = sim.execute(st, [order()])
    assert capacity[0]["blocker"] == "no_averaging_down"

    moved = state(cycle_id=CYCLE_B, books=book(CYCLE_B, [(0.30, 1000.0)]),
                  fair=fair_rows(CYCLE_B, [{"fair": 0.60}]),
                  portfolio=st.portfolio, params=st.params)
    _, capacity = sim.execute(moved, [order()])
    assert capacity[0]["blocker"] == "no_averaging_down"


def test_an_explicit_re_entry_still_needs_the_fair_to_have_moved_ten_cents():
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]), params=Params(cap_yes_usd=20.0))
    sim.execute(st, [order()])
    _, capacity = sim.execute(st, [order(reenter=True)])
    assert capacity[0]["blocker"] == "no_averaging_down"

    moved = state(cycle_id=CYCLE_B, books=book(CYCLE_B, [(0.30, 1000.0)]),
                  fair=fair_rows(CYCLE_B, [{"fair": 0.60}]),
                  portfolio=st.portfolio, params=st.params)
    _, capacity = sim.execute(moved, [order(reenter=True)])
    assert capacity[0]["blocker"] != "no_averaging_down"


def test_a_re_entry_resets_the_reference_fair_it_will_be_measured_against():
    """Otherwise a fair oscillating 10c either side of the first entry
    re-enters every cycle - averaging down with extra steps. Measured on
    2026-09-16 as five identical fills in one market."""
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]), params=Params(cap_yes_usd=20.0))
    sim.execute(st, [order()])
    second = state(cycle_id=CYCLE_B, books=book(CYCLE_B, [(0.30, 1000.0)]),
                   fair=fair_rows(CYCLE_B, [{"fair": 0.60}]),
                   portfolio=st.portfolio, params=st.params)
    sim.execute(second, [order(reenter=True)])
    assert st.portfolio.open[("m1", YES)].entry_fair_engine == pytest.approx(0.60)

    third = state(cycle_id=CYCLE_C, books=book(CYCLE_C, [(0.30, 1000.0)]),
                  fair=fair_rows(CYCLE_C, [{"fair": 0.55}]),
                  portfolio=st.portfolio, params=st.params)
    _, capacity = sim.execute(third, [order(reenter=True)])
    assert capacity[0]["blocker"] == "no_averaging_down"


# ------------------------------------------------------------- resolution


@pytest.mark.parametrize("result,outcome,expected", [
    ("Yes", YES, 1.0), ("Yes", NO, 0.0), ("No", YES, 0.0), ("No", NO, 1.0),
])
def test_a_settled_market_pays_one_or_nothing(result, outcome, expected):
    assert sim._payout_per_share(result, None, outcome) == expected


def test_a_fifty_fifty_settlement_pays_half_to_both_sides():
    assert sim._payout_per_share(None, '["0.5", "0.5"]', YES) == 0.5
    assert sim._payout_per_share(None, ["0.5", "0.5"], NO) == 0.5


def test_a_pending_uma_dispute_is_not_a_loss():
    """MODELS section 6: a null result with prices that are not 0.5/0.5 is a
    market still being argued over. Booking it as zero would invent a loss."""
    assert sim._payout_per_share(None, '["1", "0"]', YES) is None
    assert sim._payout_per_share(None, None, YES) is None


def test_yes_cash_comes_back_at_the_touch_and_no_cash_at_the_deadline():
    """MODELS 4.3 and 4.4: a NO is money locked to the end of the period
    however early the venue marks the market."""
    settled = pd.Timestamp("2026-09-16T18:00:00Z")
    deadline = pd.Timestamp("2026-09-18T20:00:00Z")
    assert sim.cash_back_time(YES, settled, deadline) == settled
    assert sim.cash_back_time(NO, settled, deadline) == deadline


def settled_portfolio(result="Yes", outcome_index=YES, shares=100.0, cost=30.0):
    params = Params()
    portfolio = sim.Portfolio(params)
    position = sim.Position(
        market_id="m1", outcome_index=outcome_index, ticker="NVDA",
        contract_type="hit_up", barrier=200.0,
        deadline=pd.Timestamp("2026-09-18T20:00:00Z"), period="weekly",
        entry_cycle_id=CYCLE_A, entry_at=pd.Timestamp("2026-09-16T14:00:00Z"),
        entry_fair=0.4, shares=shares, cost_usd=cost, fee_usd=1.2)
    portfolio.open[position.key] = position
    portfolio.cash -= cost
    resolutions = pd.DataFrame([{"market_id": "m1", "result": result,
                                 "settled_at_utc": pd.Timestamp("2026-09-17T18:00:00Z"),
                                 "outcome_prices": None}])
    return portfolio, resolutions


def test_a_winning_yes_pays_par_and_the_cash_returns():
    portfolio, resolutions = settled_portfolio()
    done = sim.settle(portfolio, resolutions, pd.Timestamp("2026-09-18T20:00:00Z"))
    assert len(done) == 1
    assert done[0].payout_usd == pytest.approx(100.0)
    assert done[0].pnl_usd == pytest.approx(70.0)
    assert portfolio.cash == pytest.approx(10_000.0 - 30.0 + 100.0)
    assert portfolio.open == {}


def test_capital_days_run_to_the_deadline_for_a_no():
    portfolio, resolutions = settled_portfolio(result="No", outcome_index=NO)
    done = sim.settle(portfolio, resolutions, pd.Timestamp("2026-09-19T20:00:00Z"))
    # 2026-09-16T14:00Z to the 2026-09-18T20:00Z deadline is 2.25 days.
    assert done[0].capital_days == pytest.approx(30.0 * 2.25)


# ----------------------------------------------------------- no lookahead


class Peeker:
    """A model that tries to read the next cycle's book. MODELS 4.1."""

    name = "peeker"

    def __init__(self, target):
        self.target = target

    def propose(self, state):
        state.books_at(self.target)
        return []


def test_a_model_that_reads_a_later_cycle_raises():
    st = state(cycle_id=CYCLE_A, loader=object())
    with pytest.raises(LookaheadError):
        Peeker(CYCLE_B).propose(st)


def test_a_model_that_reads_its_own_cycle_raises_too():
    """Its own cycle is already in `state.books`; going back through the loader
    for it would be a way to get at a frame the harness has not filtered."""
    st = state(cycle_id=CYCLE_B, loader=object())
    with pytest.raises(LookaheadError):
        Peeker(CYCLE_B).propose(st)


def test_an_earlier_cycle_is_allowed():
    class Loader:
        def books(self, cycle_id):
            return book(cycle_id, [(0.30, 1.0)])

    st = state(cycle_id=CYCLE_B, loader=Loader())
    assert len(st.books_at(CYCLE_A)) == 1


def test_resolutions_handed_to_a_model_are_only_the_settled_ones():
    """The filter lives in `data.resolutions_upto`; this pins that the state
    carries what it was given and does not reach for more."""
    st = state(resolutions=pd.DataFrame([{"market_id": "m1", "result": "Yes"}]))
    assert list(st.resolutions["market_id"]) == ["m1"]


# --------------------------------------------------------------- the signal


def test_yes_and_no_are_priced_off_the_same_fair():
    """A YES is worth `fair`, a NO `1 - fair`: one number, both sides."""
    books = pd.concat([book(CYCLE_A, [(0.20, 1000.0)], outcome_index=YES),
                       book(CYCLE_A, [(0.20, 1000.0)], outcome_index=NO)])
    fair = fair_rows(CYCLE_A, [{"fair": 0.40}])
    out = sim.candidate_edges(state(books=books, fair=fair))
    yes = out[out["outcome_index"] == YES].iloc[0]
    no = out[out["outcome_index"] == NO].iloc[0]
    assert yes["win_prob"] == pytest.approx(0.40)
    assert no["win_prob"] == pytest.approx(0.60)
    assert set(out["fair_source"]) == {"atm"}


def test_deep_otm_markets_are_not_tradeable_on_the_option_signal():
    """MODELS 3.4: FINDINGS 5.2 measured a fair value ranging 0.002-0.147 on
    one SPY barrier depending on the vol input."""
    fair = fair_rows(CYCLE_A, [{"deep_otm": True}])
    assert sim.candidate_edges(state(fair=fair)).empty


def test_a_market_already_touched_is_never_a_candidate():
    fair = fair_rows(CYCLE_A, [{"touch_pending": True}])
    assert sim.candidate_edges(state(fair=fair)).empty


def test_a_ladder_with_no_option_chain_is_skipped():
    fair = fair_rows(CYCLE_A, [{"no_chain": True}])
    assert sim.candidate_edges(state(fair=fair)).empty


def test_the_bar_above_ninety_cents_is_roi_not_three_cents_of_ev():
    """MODELS section 2: above 90c the fee is small but the spread is the whole
    edge, so a 3c EV bar would refuse every NO worth buying."""
    books = book(CYCLE_A, [(0.93, 1000.0)], outcome_index=NO)
    fair = fair_rows(CYCLE_A, [{"fair": 0.047}])
    out = sim.candidate_edges(state(books=books, fair=fair))
    assert len(out) == 1
    assert out.iloc[0]["ev"] < 0.03 and out.iloc[0]["roi"] >= 0.02


def test_the_same_edge_at_ninety_cents_fails_the_three_cent_bar():
    """The companion to the test above: it is the price that switches the bar,
    so the identical edge one cent lower is refused."""
    books = book(CYCLE_A, [(0.90, 1000.0)], outcome_index=NO)
    fair = fair_rows(CYCLE_A, [{"fair": 0.077}])
    out = sim.candidate_edges(state(books=books, fair=fair))
    assert out.empty


def test_a_market_below_the_bar_is_not_a_candidate():
    books = book(CYCLE_A, [(0.39, 1000.0)])
    fair = fair_rows(CYCLE_A, [{"fair": 0.40}])
    assert sim.candidate_edges(state(books=books, fair=fair)).empty


def test_the_limit_the_harness_walks_to_honours_both_bars():
    books = book(CYCLE_A, [(0.20, 1000.0)])
    fair = fair_rows(CYCLE_A, [{"fair": 0.40}])
    row = sim.candidate_edges(state(books=books, fair=fair)).iloc[0]
    value = 0.40 - sim.YES_RISK_PREMIUM          # a YES pays the premium
    assert row["limit_price"] == pytest.approx(min(value - 0.03, value / 1.08))


# ------------------------------------------------------------- the ledger


def test_the_exposure_of_a_no_position_has_the_opposite_sign():
    """MODELS 5.2: NO on `hit_down` is positive delta because YES on
    `hit_down` is negative."""
    params = Params()
    portfolio = sim.Portfolio(params)
    for outcome in (YES, NO):
        portfolio.open[(f"m{outcome}", outcome)] = sim.Position(
            market_id=f"m{outcome}", outcome_index=outcome, ticker="NVDA",
            contract_type="hit_down", barrier=180.0, deadline=pd.NaT, period="w",
            entry_cycle_id=CYCLE_A, entry_at=pd.Timestamp("2026-09-16T14:00:00Z"),
            entry_fair=0.4, shares=10.0, cost_usd=3.0)
    positions = list(portfolio.open.values())
    exposures = [sim.beta_dollar_delta(p, -0.01, 200.0, 2.0) for p in positions]
    assert exposures[0] == pytest.approx(-0.01 * 200 * 10 * 2.0)
    assert exposures[1] == pytest.approx(+0.01 * 200 * 10 * 2.0)
    assert sum(exposures) == pytest.approx(0.0)


# ------------------------------------------------------- the whole replay


class FakeLoader:
    """Two cycles, in memory. The level in the first cycle shrinks in the
    second."""

    def __init__(self, books_by_cycle, fair_by_cycle):
        self._books = books_by_cycle
        self._fair = fair_by_cycle

    def cycles(self):
        return sorted(self._books)

    def books(self, cycle_id):
        return self._books[cycle_id]

    def fair(self, cycle_id):
        return self._fair[cycle_id]


class BuyOnce:
    name = "buy_once"

    def __init__(self):
        self.seen = []

    def propose(self, state):
        self.seen.append(state.cycle_id)
        if state.cycle_id != CYCLE_A:
            return []
        return [order(max_usd=100.0, limit_price=0.90)]


@pytest.fixture
def replay(monkeypatch, tmp_path):
    """A two-cycle world with nothing on disk behind it."""
    books = {
        CYCLE_A: book(CYCLE_A, [(0.30, 100.0), (0.40, 100.0)]),
        CYCLE_B: book(CYCLE_B, [(0.30, 20.0), (0.40, 100.0)]),
    }
    fair = {CYCLE_A: fair_rows(CYCLE_A, [{}]), CYCLE_B: fair_rows(CYCLE_B, [{}])}
    resolutions = pd.DataFrame([{
        "market_id": "m1", "result": "Yes",
        "settled_at_utc": pd.Timestamp("2026-09-18T18:00:00Z"),
        "outcome_prices": None, "ticker": "NVDA", "contract_type": "hit_up",
        "barrier": 200.0}])

    def resolutions_upto(when):
        return resolutions[resolutions["settled_at_utc"] <= pd.Timestamp(when)]

    monkeypatch.setattr(sim.data, "resolutions_upto", resolutions_upto)
    monkeypatch.setattr(sim.factors, "beta_table", lambda tickers, now: {"NVDA": 1.9})
    monkeypatch.setattr(sim.factors, "spy_intraday_return", lambda now: -0.002)
    monkeypatch.setattr(sim, "_latest_settlement", lambda: "2026-09-19T00:00:00Z")
    monkeypatch.setattr(results.ledger_schema, "STORE", tmp_path / "store")
    monkeypatch.setattr(results, "REPORTS", tmp_path / "reports")
    return FakeLoader(books, fair)


def test_the_replay_hands_each_cycle_once_in_order(replay):
    model = BuyOnce()
    sim.run(model, loader=replay)
    assert model.seen == [CYCLE_A, CYCLE_B]


def test_a_level_that_shrank_by_the_next_cycle_still_counts_as_filled(replay):
    """The first cycle showed 100 at 0.30 and we took 50; the second cycle
    shows 20. A fill counts if the book had the size when we looked
    (CLAUDE.md, Decided), so the fill stands."""
    result = sim.run(BuyOnce(), loader=replay)
    at_thirty = [f for f in result.fills if f.price == pytest.approx(0.30)]
    assert len(at_thirty) == 1
    assert at_thirty[0].shares == pytest.approx(50.0)


def test_the_run_settles_what_has_resolved_since_the_last_cycle(replay):
    """The window is two cycles on the 16th; the market settles on the 18th.
    Reading that outcome is not lookahead - the decisions were all made
    before it - and without it MODELS 4.3's resolved-only headline is empty."""
    result = sim.run(BuyOnce(), loader=replay)
    assert len(result.settlements) == 1
    assert result.open_positions == []
    assert result.settlements[0].payout_per_share == 1.0


def test_the_report_and_the_ledger_are_written_where_they_are_asked_for(replay, tmp_path):
    result = sim.run(BuyOnce(), loader=replay)
    paths = results.write_run(result)
    assert paths["report"].exists()
    assert paths["report"].parent == tmp_path / "reports"
    assert (tmp_path / "store" / "ledger" / "buy_once" / "fills").exists()
    text = paths["report"].read_text()
    assert "Resolved PnL" in text and "MODELS 4.3" in text


def test_the_ledger_round_trips_through_its_own_schema(replay):
    import pyarrow.parquet as pq

    result = sim.run(BuyOnce(), loader=replay)
    paths = results.write_run(result)
    table = pq.read_table(paths["fills"])
    assert table.schema == sim.ledger_schema.FILLS
    assert table.num_rows == len(result.fills)


def test_a_rerun_replaces_the_ledger_rather_than_appending_to_it(replay):
    import pyarrow.parquet as pq

    first = results.write_run(sim.run(BuyOnce(), loader=replay))
    rows = pq.read_table(first["fills"]).num_rows
    second = results.write_run(sim.run(BuyOnce(), loader=replay))
    assert pq.read_table(second["fills"]).num_rows == rows


def test_cash_falls_by_what_was_spent_and_returns_at_settlement(replay):
    result = sim.run(BuyOnce(), loader=replay)
    spent = sum(f.cost_usd for f in result.fills)
    payout = sum(s.payout_usd for s in result.settlements)
    assert result.portfolio.cash == pytest.approx(10_000.0 - spent + payout)


def test_the_equity_series_has_one_row_per_cycle(replay):
    result = sim.run(BuyOnce(), loader=replay)
    assert len(result.equity) == len(result.cycles)
    assert list(result.equity["cycle_id"]) == [CYCLE_A, CYCLE_B]


def test_a_peeking_model_fails_the_run_rather_than_quietly_cheating(replay):
    with pytest.raises(LookaheadError):
        sim.run(Peeker(CYCLE_C), loader=replay)


# ------------------------------------------------- P6: whose fair is recorded


def test_a_position_records_the_fair_the_model_actually_traded_on():
    """MODELS 4.5 scores "mean fair of positions vs hit rate". For a model
    with its own fair, recording the engine's fair scores a forecaster it
    never used."""
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]),
               fair=fair_rows(CYCLE_A, [{"fair": 0.40}]))
    sim.execute(st, [order(fair=0.62, fair_source="own")])
    position = st.portfolio.open[("m1", YES)]
    assert position.entry_fair == pytest.approx(0.62)
    assert position.fair_source == "own"


def test_the_engines_fair_is_kept_separately_for_the_re_entry_rule():
    """The 10c test of MODELS 4.2 has to be evaluated by the harness at a later
    cycle, and the harness cannot recompute a model's own signal. So it keys on
    the engine's fair, which it can, and that number is carried apart from the
    one the report scores."""
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]),
               fair=fair_rows(CYCLE_A, [{"fair": 0.40}]))
    sim.execute(st, [order(fair=0.62, fair_source="own")])
    position = st.portfolio.open[("m1", YES)]
    assert position.entry_fair_engine == pytest.approx(0.40)

    later = state(cycle_id=CYCLE_B, books=book(CYCLE_B, [(0.30, 1000.0)]),
                  fair=fair_rows(CYCLE_B, [{"fair": 0.55}]),
                  portfolio=st.portfolio, params=st.params)
    _, capacity = sim.execute(later, [order(fair=0.62, reenter=True)])
    assert capacity[0]["blocker"] != "no_averaging_down"
    assert st.portfolio.open[("m1", YES)].entry_fair_engine == pytest.approx(0.55)


def test_a_position_taken_on_the_conservative_fair_is_marked_the_same_way():
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]),
               fair=fair_rows(CYCLE_A, [{"fair": 0.40}]))
    sim.execute(st, [order(fair=0.40, fair_source="atm")])
    marks = sim._marks(st, "run", "m", {"NVDA": 1.0})
    assert marks[0]["fair"] == pytest.approx(0.40)


def test_a_model_with_no_forecast_is_still_marked_on_the_engines_fair():
    """The random control has no signal, so it has nothing to be scored on -
    but its open book still has to be worth something, or its equity curve
    collapses to cash."""
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]),
               fair=fair_rows(CYCLE_A, [{"fair": 0.40}]))
    sim.execute(st, [order(fair=float("nan"), fair_source="none")])
    marks = sim._marks(st, "run", "m", {"NVDA": 1.0})
    assert marks[0]["fair"] == pytest.approx(0.40)
    assert np.isnan(st.portfolio.open[("m1", YES)].entry_fair)


def test_an_unpriced_market_is_not_a_candidate():
    fair = fair_rows(CYCLE_A, [{"fair": float("nan")}])
    assert sim.candidate_edges(state(books=book(CYCLE_A, [(0.20, 1000.0)]),
                                     fair=fair)).empty


def test_an_earnings_gap_is_not_a_candidate():
    """An earnings report between the deadline and the expiry inflates the vol
    the barrier never sees."""
    fair = fair_rows(CYCLE_A, [{"earnings_gap": True}])
    assert sim.candidate_edges(state(books=book(CYCLE_A, [(0.20, 1000.0)]),
                                     fair=fair)).empty


def test_the_concentration_percentages_state_the_dollars_they_are_a_share_of():
    """A rate with no count. "Largest single position 25% of dollars" against a
    total the report never prints is not a number anyone can check."""
    st = state(books=book(CYCLE_A, [(0.30, 1000.0)]))
    sim.execute(st, [order()])
    result = sim.Result(model="m", params=st.params, run_id="r",
                        cycles=[CYCLE_A], fills=[], capacity=pd.DataFrame(),
                        marks=pd.DataFrame(), equity=pd.DataFrame(), settlements=[],
                        open_positions=list(st.portfolio.open.values()),
                        portfolio=st.portfolio, seconds=1.0,
                        settled_through="2026-09-18T00:00:00Z")
    text = results.report_markdown(result)
    deployed = sum(p.cost_usd for p in result.open_positions)
    assert f"${deployed:,.2f} deployed" in text
