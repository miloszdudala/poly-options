"""The decision steps after pricing - fee, bar, offers, sizing, fills, account,
settlement - and the replay that runs them, on in-memory books.

`world()` stands in for the store and the pricing cache: fixed books, priced
markets and resolutions per cycle.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

import replay
from pipeline import config as configs, venues
from pipeline.accounts import BudgetAccount, period_key
from pipeline.core import Cycle, VARIANTS, variant
from pipeline.fills import walk_ladder
from pipeline.offers import Bar, make_offers
from pipeline.settle import payout_per_share
from pipeline.sizing import Model, Order
from reporting import results

MON = "20260914T140000Z-aaaaaa"      # Monday 14 Sep 2026, 10:00 ET
TUE = "20260915T140000Z-bbbbbb"
NEXT_MON = "20260921T140000Z-cccccc"
DEADLINE = pd.Timestamp("2026-09-18T20:00:00Z")
TODAYS_BAR = Bar()
FEE = configs.load("mvp")["fee"]


def fee(price):
    return 0.04 * price * (1.0 - price)


@pytest.fixture(autouse=True)
def no_recorded_fee_schedules(monkeypatch):
    """Synthetic markets have none, and these tests must not read the store."""
    monkeypatch.setattr(replay.store, "_poly_fee_history", lambda: {})


def stamp(cycle_id: str) -> pd.Timestamp:
    return pd.Timestamp(replay.store.cycle_start(cycle_id))


def asks(cycle_id, market_id="m1", yes=((0.30, 100.0),), no=((0.75, 100.0),)):
    rows = []
    for index, levels in ((0, yes), (1, no)):
        for rank, (price, size) in enumerate(levels):
            rows.append({"cycle_id": cycle_id, "fetched_at_utc": stamp(cycle_id),
                         "market_id": market_id, "outcome_index": index, "side": "ask",
                         "depth_rank": rank, "price": price, "size": size})
    return pd.DataFrame(rows)


def priced(market_id="m1", fair=0.40, ticker="NVDA", up=True, gate=None):
    return {"market_id": market_id, "ticker": ticker, "deadline": DEADLINE, "fair": fair,
            "yes_direction": "up" if up else "down", "yes_vol": "wild",
            "contract_type": "hit_up" if up else "hit_down", "level": 200.0,
            "period": "weekly", "gate": gate, "tradeable": gate is None}


def resolved(market_id="m1", result="Yes", when="2026-09-18T20:00:00Z"):
    return {"market_id": market_id, "result": result, "outcome_prices": None,
            "settled_at_utc": pd.Timestamp(when)}


@pytest.fixture
def world(monkeypatch):
    """`world(cycles, resolutions)` makes the replay read exactly these."""
    def make(cycles: dict, resolutions: list[dict] | None = None):
        frame = pd.DataFrame(resolutions or [], columns=["market_id", "result",
                                                         "outcome_prices", "settled_at_utc"])
        monkeypatch.setattr(replay.store, "session_cycles", lambda date: sorted(cycles))
        monkeypatch.setattr(replay.cache, "priced",
                            lambda cycle, config, use_cache=True: pd.DataFrame(cycles[cycle.id][1]))
        monkeypatch.setattr(Cycle, "books", property(lambda self: cycles[self.id][0]))
        monkeypatch.setattr(venues, "polymarket_resolutions", lambda upto: frame[
            pd.to_datetime(frame["settled_at_utc"], utc=True) <= pd.Timestamp(upto)])
    return make


def run(config="mvp", overrides=None):
    return replay.run(configs.load(config, overrides), ["window"],
                      settle_through="2026-12-31T00:00:00Z")


# ------------------------------------------------------------------ the bar


def test_the_mvp_config_holds_todays_bar_unchanged():
    """EV >= 3c and return >= 8%; above 90c, return >= 2%; YES pays 2c first."""
    bar = configs.load("mvp")["bar"]
    assert bar == {"min_ev": 0.03, "min_roi": 0.08, "dear_price": 0.90, "dear_min_ev": 0.0,
                   "dear_min_roi": 0.02, "yes_premium": 0.02}
    assert Bar(**bar) == TODAYS_BAR


def test_the_fee_is_polymarkets_formula_at_the_finance_rate():
    polymarket = VARIANTS["fee"]["polymarket"](dict(FEE))
    assert polymarket(0.30) == pytest.approx(0.04 * 0.30 * 0.70)
    assert polymarket(0.50) == pytest.approx(0.01)


def test_the_fee_is_the_markets_own_schedule_at_that_moment(monkeypatch):
    """Point in time: the holiday applies inside it, never before it was listed."""
    at = [pd.Timestamp(t, tz="UTC").value for t in ("2026-10-01", "2026-10-20", "2026-11-02")]
    schedules = [(0.04, 1.0), (0.0, 1.0), (0.04, 1.0)]
    monkeypatch.setattr(replay.store, "_poly_fee_history", lambda: {"m": (at, schedules)})
    polymarket = VARIANTS["fee"]["polymarket"](dict(FEE))
    when = lambda day: pd.Timestamp(day, tz="UTC")  # noqa: E731
    assert polymarket(0.5, "m", when("2026-09-01")) == pytest.approx(0.01)   # first record
    assert polymarket(0.5, "m", when("2026-10-20")) == 0.0                   # listed at this instant
    assert polymarket(0.5, "m", when("2026-11-02")) == pytest.approx(0.01)
    assert polymarket(0.5, "unrecorded", when("2026-10-25")) == pytest.approx(0.01)


def test_kalshis_fee_is_quadratic_at_seven_percent():
    kalshi = VARIANTS["fee"]["kalshi"]({"rate": 0.07})
    assert kalshi(0.5) == pytest.approx(0.07 * 0.25)


def test_a_yes_is_charged_the_premium_and_a_no_is_not():
    assert TODAYS_BAR.value(0.40, "YES") == pytest.approx(0.38)
    assert TODAYS_BAR.value(0.60, "NO") == pytest.approx(0.60)


def test_above_ninety_cents_the_bar_is_roi_not_three_cents():
    assert TODAYS_BAR.clears(0.953, 0.93 + 0.04 * 0.07, 0.93)
    assert not TODAYS_BAR.clears(0.923, 0.90 + 0.04 * 0.10, 0.90)


def test_the_limit_honours_both_floors():
    assert TODAYS_BAR.limit(0.38, 0.20) == pytest.approx(min(0.38 - 0.03, 0.38 / 1.08))


def test_the_control_bar_has_no_floor():
    bar = Bar(**configs.load("control")["bar"])
    assert bar.clears(0.10, 0.50, 0.48)
    assert bar.limit(0.10, 0.48) == pytest.approx(1.10)


# ---------------------------------------------------------------- the ladder


def ladder(levels):
    return pd.DataFrame([{"depth_rank": r, "price": p, "size": s} for r, (p, s) in enumerate(levels)])


def test_the_walk_takes_half_of_each_level_and_stops_at_the_limit():
    taken = walk_ladder(ladder([(0.30, 100.0), (0.32, 100.0), (0.40, 100.0)]), 0.36,
                        math.inf, fee)
    assert [t["shares"] for t in taken] == [50.0, 50.0]


def test_the_walk_stops_when_the_money_runs_out():
    taken = walk_ladder(ladder([(0.30, 1000.0)]), 0.5, 30.84, fee)
    assert taken[0]["shares"] == pytest.approx(100.0)


def test_a_walk_under_one_dollar_is_no_fill():
    assert walk_ladder(ladder([(0.30, 4.0)]), 0.5, math.inf, fee) == []


def test_take_fraction_and_minimum_are_settings():
    taken = walk_ladder(ladder([(0.30, 100.0)]), 0.5, math.inf, fee, take_fraction=1.0)
    assert taken[0]["shares"] == 100.0


# ------------------------------------------------------------------- offers


def offers_for(prices, fair=0.40, held=frozenset(), bar=TODAYS_BAR, up=True, gate=None):
    frame = pd.DataFrame([priced(fair=fair, up=up, gate=gate)])
    venue_fee = VARIANTS["fee"]["polymarket"](dict(FEE))
    return make_offers(frame, asks(MON, **prices), venue_fee, bar, {}, set(held))[0]


def test_an_offer_carries_what_a_model_needs():
    [bet] = offers_for({"yes": ((0.20, 100.0),), "no": ((0.85, 100.0),)})
    assert (bet.market_id, bet.side) == ("m1", "YES")
    assert bet.value == pytest.approx(0.38) and bet.cost == pytest.approx(0.2064)
    assert bet.max_usd == pytest.approx(50 * 0.2064)


@pytest.mark.parametrize("up,side,direction,vol", [
    (True, "YES", "up", "wild"), (False, "NO", "up", "calm"),
    (False, "YES", "down", "wild"), (True, "NO", "down", "calm"),
])
def test_direction_and_vol_of_a_hit_bet(up, side, direction, vol):
    prices = ({"yes": ((0.10, 100.0),), "no": ((0.95, 100.0),)} if side == "YES"
              else {"yes": ((0.95, 100.0),), "no": ((0.30, 100.0),)})
    [bet] = offers_for(prices, up=up)
    assert (bet.side, bet.direction, bet.vol) == (side, direction, vol)


def test_gated_held_and_unattractive_markets_are_not_offered():
    good = {"yes": ((0.20, 100.0),), "no": ((0.85, 100.0),)}
    assert offers_for({"yes": ((0.39, 100.0),), "no": ((0.62, 100.0),)}) == []
    assert offers_for(good, held={"m1"}) == []
    assert offers_for(good, gate="deep_otm") == []


# ------------------------------------------------------------------ the replay


@variant("sizing", "take_all_test")
class TakeAll(Model):
    """A throwaway model, registered here to prove the format is enough."""

    seen: list = []

    def decide(self, offers, book):
        TakeAll.seen.append((list(offers), book))
        return [Order(bet, usd=bet.max_usd) for bet in offers]


def two_weeks():
    cycles = {MON: (asks(MON, "a", yes=((0.20, 1000.0),)), [priced("a")]),
              NEXT_MON: (asks(NEXT_MON, "b", yes=((0.20, 1000.0),)), [priced("b")])}
    return cycles, [resolved("a", "Yes", "2026-09-18T20:00:00Z")]


def test_a_new_model_runs_through_the_harness_with_one_override(world):
    world({MON: (asks(MON, yes=((0.20, 100.0), (0.25, 100.0))), [priced()])},
          [resolved(result="Yes")])
    result = run(overrides=["sizing.use=take_all_test"])
    [position] = result.positions
    assert position.shares == pytest.approx(100.0) and position.payout_usd == 100.0
    expected = sum(50 * (0.38 - (p + fee(p))) for p in (0.20, 0.25))
    assert position.ev_usd == pytest.approx(expected)


def test_a_market_is_bought_once_on_one_side(world):
    TakeAll.seen = []
    world({MON: (asks(MON, yes=((0.20, 100.0),)), [priced()]),
           TUE: (asks(TUE, yes=((0.20, 100.0),)), [priced()])})
    result = run(overrides=["sizing.use=take_all_test"])
    assert len(result.positions) == 1 and len(TakeAll.seen) == 1


def test_the_book_shows_holdings_by_direction_and_vol(world):
    TakeAll.seen = []
    world({MON: (asks(MON, "a", yes=((0.20, 100.0),)), [priced("a")]),
           TUE: (asks(TUE, "b", yes=((0.20, 100.0),)), [priced("b", up=False)])})
    run(overrides=["sizing.use=take_all_test"])
    book = TakeAll.seen[1][1]
    assert book.usd_by_direction()["up"] == pytest.approx(50 * 0.2064)
    assert book.usd_by_vol()["wild"] == pytest.approx(50 * 0.2064) and book.cash == math.inf


def test_random_control_pays_at_most_two_cents_over_the_best_ask(world):
    book = asks(MON, yes=((0.30, 100.0), (0.31, 100.0), (0.40, 100.0)), no=((0.99, 1.0),))
    world({MON: (book, [priced(fair=0.10)])})
    [position] = run("control").positions
    assert position.side == "YES" and position.shares == pytest.approx(100.0)


def test_gate_reasons_are_counted_once_per_market(world):
    world({MON: (asks(MON), [priced(gate="deep_otm")]), TUE: (asks(TUE), [priced(gate="deep_otm")])})
    assert run().skipped == {"deep_otm": 1}


# ------------------------------------------------------------------ accounts


def test_max_takes_as_much_as_the_book_allows(world):
    world(*two_weeks())
    assert [p.shares for p in run().positions] == [500.0, 500.0]


def test_compound_starts_small_and_reinvests_what_comes_back(world):
    cycles, res = two_weeks()
    cycles[NEXT_MON] = (asks(NEXT_MON, "b", yes=((0.20, 10_000.0),)), [priced("b")])
    world(cycles, res)
    first, second = sorted(run(overrides=["account={use: compound, start: 100}"]).positions,
                           key=lambda p: p.entry_at)
    assert first.cost_usd == pytest.approx(100.0)
    assert second.cost_usd == pytest.approx(100.0 / 0.2064)


def test_budget_resets_each_week_and_does_not_roll_over(world):
    cycles, res = two_weeks()
    cycles[TUE] = (asks(TUE, "c", yes=((0.20, 1000.0),)), [priced("c")])
    world(cycles, res)
    cost = {p.market_id: p.cost_usd for p in
            run(overrides=["account={use: budget, per_period: 30}"]).positions}
    assert cost["a"] == pytest.approx(30.0) and "c" not in cost and cost["b"] == pytest.approx(30.0)


def test_period_keys_follow_new_york_time():
    assert period_key(pd.Timestamp("2026-10-01T03:00:00Z"), "monthly") == "2026-09"
    assert period_key(pd.Timestamp("2026-09-20T12:00:00Z"), "weekly") == "2026-09-14"
    assert period_key(pd.Timestamp("2026-09-20T12:00:00Z"), "daily") == "2026-09-20"
    account = BudgetAccount(per_period=5.0)
    account.start_cycle(pd.Timestamp("2026-09-14T14:00:00Z"), "weekly")
    assert account.cash() == 5.0


# ---------------------------------------------------------------- settlement


@pytest.mark.parametrize("result,side,expected", [
    ("Yes", "YES", 1.0), ("Yes", "NO", 0.0), ("No", "YES", 0.0), ("No", "NO", 1.0)])
def test_payout_is_par_or_nothing(result, side, expected):
    assert payout_per_share(result, None, side) == expected


def test_a_fifty_fifty_pays_half_and_a_dispute_pays_nothing_yet():
    assert payout_per_share(None, '["0.5", "0.5"]', "YES") == 0.5
    assert payout_per_share(None, '["0.9", "0.1"]', "YES") is None


def test_a_resolution_after_a_cycle_is_not_seen_by_it(world):
    TakeAll.seen = []
    world({MON: (asks(MON, "a", yes=((0.20, 100.0),)), [priced("a")]),
           TUE: (asks(TUE, "b", yes=((0.20, 100.0),)), [priced("b")])},
          [resolved("a", "Yes", "2026-09-15T15:00:00Z")])
    run(overrides=["sizing.use=take_all_test"])
    assert TakeAll.seen[1][1].settled == []


# ------------------------------------------------------------------ reports


def test_the_summary_sets_promised_ev_against_realised_pnl(world):
    world(*two_weeks())
    m = results.summary(run())
    assert m["bets"] == 2 and m["settled"] == 1
    assert m["pnl"] == pytest.approx(500.0 - 500 * 0.2064)
    assert m["settled_promised_ev"] == pytest.approx(500 * (0.38 - 0.2064))
    assert list(results.by_period(run()).index) == ["2026-09-14", "2026-09-21"]


def test_the_ledger_round_trips_through_its_schema(world, tmp_path, monkeypatch):
    import pyarrow.parquet as pq

    monkeypatch.setattr(results.ledger_schema, "STORE", tmp_path / "store")
    monkeypatch.setattr(results, "REPORTS", tmp_path / "reports")
    world(*two_weeks())
    paths = results.write_run(run(), tag="t")
    assert paths["fills"].parent.parent.name == "mvp_t"
    assert pq.read_table(paths["fills"]).schema == results.ledger_schema.FILLS
    assert pq.read_table(results.write_run(run(), tag="t")["bets"]).num_rows == 2
