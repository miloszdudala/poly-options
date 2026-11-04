"""The harness, on synthetic books: offers, fills, accounts, settlement, and
the formats for models and types.

Everything here is built in memory. `FakeType` stands in for a real type: it
serves fixed books, fair values and resolutions per cycle.
"""
from __future__ import annotations

import math
import textwrap

import pandas as pd
import pytest

import bet_types
import results
import run
import sim
from models import ANY_PRICE, NO, TODAYS_BAR, YES, Bar, Book, Model, Order

MON = "20260914T140000Z-aaaaaa"      # Monday 14 Sep 2026, 10:00 ET
TUE = "20260915T140000Z-bbbbbb"
NEXT_MON = "20260921T140000Z-cccccc"
DEADLINE = pd.Timestamp("2026-09-18T20:00:00Z")


def stamp(cycle_id: str) -> pd.Timestamp:
    return pd.Timestamp(sim.data.cycle_start(cycle_id))


def asks(cycle_id, market_id="m1", yes=((0.30, 100.0),), no=((0.75, 100.0),)):
    """Ask ladders for both tokens of one market, best price first."""
    rows = []
    for index, levels in ((0, yes), (1, no)):
        for rank, (price, size) in enumerate(levels):
            rows.append({"cycle_id": cycle_id, "fetched_at_utc": stamp(cycle_id),
                         "market_id": market_id, "outcome_index": index, "side": "ask",
                         "depth_rank": rank, "price": price, "size": size})
    return pd.DataFrame(rows)


def priced(market_id="m1", fair=0.40, ticker="NVDA", up=True, **extra):
    return {"market_id": market_id, "ticker": ticker, "deadline": DEADLINE, "fair": fair,
            "yes_direction": "up" if up else "down", "yes_vol": "wild",
            "contract_type": "hit_up" if up else "hit_down", "level": 200.0,
            "period": "weekly", **extra}


class FakeType(bet_types.Polymarket):
    """Fixed books, fairs and resolutions; Polymarket's fee."""

    name = "fake"
    period = "weekly"

    def __init__(self, cycles: dict, resolutions: list[dict] | None = None):
        super().__init__()
        self._cycles = cycles
        self._resolutions = pd.DataFrame(resolutions or [], columns=[
            "market_id", "result", "outcome_prices", "settled_at_utc"])

    def cycles(self, dates):
        return sorted(self._cycles)

    def books(self, cycle_id):
        return self._cycles[cycle_id][0]

    def priced(self, cycle_id):
        return pd.DataFrame(self._cycles[cycle_id][1])

    def resolutions(self, upto):
        frame = self._resolutions
        return frame[pd.to_datetime(frame["settled_at_utc"], utc=True) <= pd.Timestamp(upto)]


def resolved(market_id="m1", result="Yes", when="2026-09-18T20:00:00Z"):
    return {"market_id": market_id, "result": result, "outcome_prices": None,
            "settled_at_utc": pd.Timestamp(when)}


class TakeAll(Model):
    """A throwaway model, written here to prove the format is enough: it takes
    every offer, as large as the book allows."""

    name = "take_all"
    bar = TODAYS_BAR

    def decide(self, offers, book):
        return [Order(bet, usd=bet.max_usd) for bet in offers]


class Recorder(TakeAll):
    """Takes everything, and keeps what it was shown."""

    name = "recorder"

    def __init__(self):
        self.seen: list[tuple[list, Book]] = []

    def decide(self, offers, book):
        self.seen.append((list(offers), book))
        return super().decide(offers, book)


def replay(cycles, resolutions=None, model=None, account=None):
    model = model or TakeAll()
    return sim.run(model, FakeType(cycles, resolutions), account or sim.MaxAccount(), [],
                   settle_through="2026-12-31T00:00:00Z")


# ------------------------------------------------------------------ the bar


def test_the_fee_is_four_percent_of_the_cheaper_side():
    fee = FakeType({}).fee
    assert fee(0.30) == pytest.approx(0.012)
    assert fee(0.95) == pytest.approx(0.002)
    assert fee(0.50) == pytest.approx(0.02)


def test_todays_bar_is_unchanged():
    """EV >= 3c and return >= 8%; above 90c, return >= 2%; YES pays 2c first."""
    assert (TODAYS_BAR.min_ev, TODAYS_BAR.min_roi) == (0.03, 0.08)
    assert (TODAYS_BAR.dear_price, TODAYS_BAR.dear_min_ev, TODAYS_BAR.dear_min_roi) == (0.90, 0.0, 0.02)
    assert TODAYS_BAR.yes_premium == 0.02


def test_a_yes_is_charged_the_premium_and_a_no_is_not():
    assert TODAYS_BAR.value(0.40, YES) == pytest.approx(0.38)
    assert TODAYS_BAR.value(0.60, NO) == pytest.approx(0.60)


def test_above_ninety_cents_the_bar_is_roi_not_three_cents():
    value = 0.953
    cost = 0.93 + 0.04 * 0.07
    assert TODAYS_BAR.clears(value, cost, 0.93)          # EV < 3c, ROI >= 2%
    assert not TODAYS_BAR.clears(0.923, 0.90 + 0.04 * 0.10, 0.90)   # same edge at 90c


def test_the_limit_honours_both_floors():
    value = 0.38
    assert TODAYS_BAR.limit(value, 0.20) == pytest.approx(min(value - 0.03, value / 1.08))


def test_any_price_has_no_floor():
    assert ANY_PRICE.clears(0.10, 0.50, 0.48)
    assert ANY_PRICE.limit(0.10, 0.48) == pytest.approx(1.10)


# ---------------------------------------------------------------- the ladder


def ladder(levels):
    return pd.DataFrame([{"depth_rank": r, "price": p, "size": s}
                         for r, (p, s) in enumerate(levels)])


def fee(price):
    return 0.04 * min(price, 1.0 - price)


def test_the_walk_takes_half_of_each_level_and_stops_at_the_limit():
    taken = sim.walk_ladder(ladder([(0.30, 100.0), (0.32, 100.0), (0.40, 100.0)]),
                            limit=0.36, budget_usd=math.inf, fee=fee)
    assert [t["shares"] for t in taken] == [50.0, 50.0]
    assert [t["depth_rank"] for t in taken] == [0, 1]


def test_the_walk_stops_when_the_money_runs_out():
    taken = sim.walk_ladder(ladder([(0.30, 1000.0)]), limit=0.5, budget_usd=31.2, fee=fee)
    assert taken[0]["shares"] == pytest.approx(100.0)
    assert taken[0]["cost_usd"] == pytest.approx(31.2)


def test_a_walk_under_one_dollar_is_no_fill():
    assert sim.walk_ladder(ladder([(0.30, 4.0)]), limit=0.5, budget_usd=math.inf,
                           fee=fee) == []


# ------------------------------------------------------------------- offers


def offers_for(prices, fair=0.40, held=frozenset(), bar=TODAYS_BAR, up=True):
    book = asks(MON, **prices)
    return sim.make_offers(FakeType({}), bar, pd.DataFrame([priced(fair=fair, up=up)]),
                           book, set(held))[0]


def test_an_offer_carries_what_a_model_needs():
    [bet] = offers_for({"yes": ((0.20, 100.0),), "no": ((0.85, 100.0),)})
    assert (bet.market_id, bet.side, bet.type) == ("m1", YES, "fake")
    assert bet.win_prob == pytest.approx(0.40) and bet.value == pytest.approx(0.38)
    assert bet.cost == pytest.approx(0.208)
    assert bet.ev == pytest.approx(0.38 - 0.208)
    assert bet.max_usd == pytest.approx(50 * 0.208)
    assert bet.deadline == DEADLINE


@pytest.mark.parametrize("up,side,direction,vol", [
    (True, YES, "up", "wild"), (False, NO, "up", "calm"),
    (False, YES, "down", "wild"), (True, NO, "down", "calm"),
])
def test_direction_and_vol_of_a_hit_bet(up, side, direction, vol):
    prices = ({"yes": ((0.10, 100.0),), "no": ((0.95, 100.0),)} if side == YES
              else {"yes": ((0.95, 100.0),), "no": ((0.30, 100.0),)})
    [bet] = offers_for(prices, up=up)
    assert (bet.side, bet.direction, bet.vol) == (side, direction, vol)


def test_offers_below_the_bar_and_already_held_markets_are_left_out():
    assert offers_for({"yes": ((0.39, 100.0),), "no": ((0.62, 100.0),)}) == []
    assert offers_for({"yes": ((0.20, 100.0),), "no": ((0.85, 100.0),)}, held={"m1"}) == []


def test_offers_come_best_return_first():
    book = pd.concat([asks(MON, "a", yes=((0.30, 100.0),)), asks(MON, "b", yes=((0.20, 100.0),))])
    rows = pd.DataFrame([priced("a"), priced("b")])
    offers, _ = sim.make_offers(FakeType({}), TODAYS_BAR, rows, book, set())
    assert [b.market_id for b in offers] == ["b", "a"]


# ------------------------------------------------------- models and the format


def test_a_throwaway_model_runs_through_the_harness():
    result = replay({MON: (asks(MON, yes=((0.20, 100.0), (0.25, 100.0))), [priced()])},
                    [resolved(result="Yes")])
    [position] = result.positions
    assert position.side == YES and position.shares == pytest.approx(100.0)
    assert position.payout_usd == pytest.approx(100.0)
    assert position.pnl_usd == pytest.approx(100.0 - position.cost_usd)
    # promised EV: value 0.38 against what each level actually cost
    expected = sum(50 * (0.38 - (p + fee(p))) for p in (0.20, 0.25))
    assert position.ev_usd == pytest.approx(expected)


def test_a_market_is_bought_once_on_one_side():
    model = Recorder()
    cycles = {MON: (asks(MON, yes=((0.20, 100.0),)), [priced()]),
              TUE: (asks(TUE, yes=((0.20, 100.0),)), [priced()])}
    result = replay(cycles, model=model)
    assert len(result.positions) == 1
    assert len(model.seen) == 1          # nothing left to offer on Tuesday


def test_the_book_shows_holdings_by_direction_and_vol():
    model = Recorder()
    cycles = {MON: (asks(MON, "a", yes=((0.20, 100.0),)), [priced("a")]),
              TUE: (asks(TUE, "b", yes=((0.20, 100.0),)), [priced("b", up=False)])}
    replay(cycles, model=model)
    book = model.seen[1][1]
    assert book.usd_by_direction()["up"] == pytest.approx(50 * 0.208)
    assert book.usd_by_vol() == {"calm": 0.0, "wild": pytest.approx(50 * 0.208)}
    assert book.cash == math.inf


def test_an_order_is_capped_by_the_offer_and_by_its_own_limit():
    class Greedy(TakeAll):
        name = "greedy"

        def decide(self, offers, book):
            return [Order(offers[0], usd=1e9, limit=0.22)]

    result = replay({MON: (asks(MON, yes=((0.20, 100.0), (0.25, 100.0))), [priced()])},
                    model=Greedy())
    [position] = result.positions
    assert position.shares == pytest.approx(50.0)        # the 0.25 level is past 0.22


def test_edge_taker_takes_every_offer_whole():
    from models.edge_taker import EdgeTaker

    result = replay({MON: (pd.concat([asks(MON, "a", yes=((0.20, 100.0),)),
                                      asks(MON, "b", yes=((0.25, 40.0),))]),
                           [priced("a"), priced("b")])}, model=EdgeTaker())
    assert sorted((p.market_id, p.shares) for p in result.positions) == [("a", 50.0), ("b", 20.0)]


def test_random_control_pays_at_most_two_cents_over_the_best_ask():
    from models.random_control import RandomControl

    book = asks(MON, yes=((0.30, 100.0), (0.31, 100.0), (0.40, 100.0)), no=((0.99, 1.0),))
    result = replay({MON: (book, [priced(fair=0.10)])}, model=RandomControl())
    [position] = result.positions
    assert position.side == YES and position.shares == pytest.approx(100.0)


def test_a_new_model_and_a_new_type_are_found_without_editing_any_file(tmp_path):
    models_dir, types_dir = tmp_path / "models", tmp_path / "types"
    models_dir.mkdir()
    types_dir.mkdir()
    (models_dir / "only_no.py").write_text(textwrap.dedent('''
        from models import TODAYS_BAR, Model, Order

        class OnlyNo(Model):
            name = "only_no"
            bar = TODAYS_BAR

            def decide(self, offers, book):
                return [Order(b, usd=b.max_usd) for b in offers if b.side == "NO"]
    '''))
    (types_dir / "_shared.py").write_text("FAIR = 0.40\n")
    (types_dir / "fixed.py").write_text(textwrap.dedent(f'''
        import pandas as pd
        import bet_types
        from _shared import FAIR

        class Fixed(bet_types.Polymarket):
            name = "fixed"
            period = "weekly"

            def cycles(self, dates):
                return ["{MON}"]

            def books(self, cycle_id):
                rows = [(0, 0.20), (1, 0.50)]
                return pd.DataFrame([{{"market_id": "m1", "outcome_index": i, "side": "ask",
                                      "depth_rank": 0, "price": p, "size": 100.0,
                                      "fetched_at_utc": pd.Timestamp("2026-09-14T14:00Z")}}
                                     for i, p in rows])

            def priced(self, cycle_id):
                return pd.DataFrame([{{"market_id": "m1", "ticker": "X", "fair": FAIR,
                                      "deadline": pd.Timestamp("2026-09-18T20:00Z"),
                                      "yes_direction": "up", "yes_vol": "wild",
                                      "contract_type": "hit_up", "level": 1.0,
                                      "period": "weekly"}}])

            def resolutions(self, upto):
                return pd.DataFrame(columns=["market_id", "result", "outcome_prices",
                                             "settled_at_utc"])
    '''))
    found_models = run.discover_models(models_dir)
    found_types = run.discover_types(types_dir)
    assert set(found_models) == {"only_no"} and set(found_types) == {"fixed"}
    [(result, m)] = run.run_matrix([], model_classes=found_models, type_classes=found_types)
    assert [(p.side, p.market_id) for p in result.positions] == [(NO, "m1")]
    assert m["bets"] == 1


def test_the_real_folders_hold_the_ported_models_and_the_two_hit_types():
    assert {"edge_taker", "random_control"} <= set(run.discover_models())
    assert {"poly-hit-weekly-stock", "poly-hit-monthly-stock"} <= set(run.discover_types())


# ------------------------------------------------------------------ accounts


def two_weeks():
    """A bet on Monday that settles a winner on Friday, and another market on
    offer the Monday after."""
    cycles = {MON: (asks(MON, "a", yes=((0.20, 1000.0),)), [priced("a")]),
              NEXT_MON: (asks(NEXT_MON, "b", yes=((0.20, 1000.0),)), [priced("b")])}
    return cycles, [resolved("a", "Yes", "2026-09-18T20:00:00Z")]


def test_max_takes_as_much_as_the_book_allows():
    cycles, res = two_weeks()
    result = replay(cycles, res, account=sim.MaxAccount())
    assert [p.shares for p in result.positions] == [500.0, 500.0]
    assert results.summary(result)["money_in"] == pytest.approx(2 * 500 * 0.208)


def test_compound_starts_small_and_reinvests_what_comes_back():
    cycles, res = two_weeks()
    cycles[NEXT_MON] = (asks(NEXT_MON, "b", yes=((0.20, 10_000.0),)), [priced("b")])
    account = sim.CompoundAccount(start=100.0)
    result = replay(cycles, res, account=account)
    first, second = sorted(result.positions, key=lambda p: p.entry_at)
    assert first.cost_usd == pytest.approx(100.0)
    shares = 100.0 / 0.208
    assert second.cost_usd == pytest.approx(shares)       # the payout, all of it
    assert account.balance == pytest.approx(0.0, abs=1e-6)


def test_budget_resets_each_week_and_does_not_roll_over():
    cycles, res = two_weeks()
    cycles[TUE] = (asks(TUE, "c", yes=((0.20, 1000.0),)), [priced("c")])
    result = replay(cycles, res, account=sim.BudgetAccount(per_period=30.0))
    cost = {p.market_id: p.cost_usd for p in result.positions}
    assert cost["a"] == pytest.approx(30.0)
    assert "c" not in cost                    # Tuesday: the week's budget is spent
    assert cost["b"] == pytest.approx(30.0)   # a new week, not 30 + the payout


def test_a_monthly_budget_resets_on_the_first_in_new_york():
    assert sim.period_key(pd.Timestamp("2026-10-01T03:00:00Z"), "monthly") == "2026-09"
    assert sim.period_key(pd.Timestamp("2026-10-01T05:00:00Z"), "monthly") == "2026-10"
    assert sim.period_key(pd.Timestamp("2026-09-20T12:00:00Z"), "weekly") == "2026-09-14"
    assert sim.period_key(pd.Timestamp("2026-09-20T12:00:00Z"), "daily") == "2026-09-20"


def test_the_budget_account_needs_its_amount():
    with pytest.raises(ValueError):
        sim.make_account("budget")
    assert sim.make_account("compound", start=50.0).cash() == 50.0


# ---------------------------------------------------------------- settlement


@pytest.mark.parametrize("result,side,expected", [
    ("Yes", YES, 1.0), ("Yes", NO, 0.0), ("No", YES, 0.0), ("No", NO, 1.0),
])
def test_payout_is_par_or_nothing(result, side, expected):
    assert sim.payout_per_share(result, None, side) == expected


def test_a_fifty_fifty_pays_half_and_a_dispute_pays_nothing_yet():
    assert sim.payout_per_share(None, '["0.5", "0.5"]', YES) == 0.5
    assert sim.payout_per_share(None, '["0.9", "0.1"]', YES) is None


def test_a_resolution_after_a_cycle_is_not_seen_by_it():
    model = Recorder()
    cycles = {MON: (asks(MON, "a", yes=((0.20, 100.0),)), [priced("a")]),
              TUE: (asks(TUE, "b", yes=((0.20, 100.0),)), [priced("b")])}
    replay(cycles, [resolved("a", "Yes", "2026-09-15T15:00:00Z")], model=model)
    assert model.seen[1][1].settled == []      # Tuesday 14:00Z is before 15:00Z


# ------------------------------------------------------------------ reports


def test_the_summary_sets_promised_ev_against_realised_pnl():
    cycles, res = two_weeks()
    result = replay(cycles, res)
    m = results.summary(result)
    assert m["bets"] == 2 and m["settled"] == 1 and m["open"] == 1
    assert m["pnl"] == pytest.approx(500.0 - 500 * 0.208)
    assert m["settled_promised_ev"] == pytest.approx(500 * (0.38 - 0.208))
    assert m["realised_over_promised"] == pytest.approx(m["pnl"] / m["settled_promised_ev"])
    assert m["periods_settled"] == 1


def test_pnl_is_grouped_by_the_week_of_entry():
    cycles, res = two_weeks()
    table = results.by_period(replay(cycles, res))
    assert list(table.index) == ["2026-09-14", "2026-09-21"]
    assert table.loc["2026-09-21", "settled"] == 0


def test_the_ledger_round_trips_through_its_schema(tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    monkeypatch.setattr(results.ledger_schema, "STORE", tmp_path / "store")
    monkeypatch.setattr(results, "REPORTS", tmp_path / "reports")
    cycles, res = two_weeks()
    result = replay(cycles, res)
    paths = results.write_run(result, tag="t")
    assert paths["fills"].parent.parent.name == "take_all__fake_t"
    assert pq.read_table(paths["fills"]).schema == results.ledger_schema.FILLS
    assert pq.read_table(paths["bets"]).num_rows == 2
    text = paths["report"].read_text()
    assert "Promised against realised" in text
    again = results.write_run(result, tag="t")
    assert pq.read_table(again["bets"]).num_rows == 2      # replaced, not appended


def test_a_custom_bar_is_used_for_the_offers():
    class Strict(TakeAll):
        name = "strict"
        bar = Bar(min_ev=0.30)

    result = replay({MON: (asks(MON, yes=((0.20, 100.0),)), [priced()])}, model=Strict())
    assert result.positions == []
