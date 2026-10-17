import ast
import datetime as dt
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from conftest import LIVE, book_rows, fair_row

import trader
from ledger import Ledger

NOW = dt.datetime(2026, 10, 20, 15, 0, tzinfo=dt.timezone.utc)
CYCLE = "20261020T144500Z"


class FakeVenue:
    """A venue whose book is whatever the test says and whose buys fill fully."""

    def __init__(self, asks=((0.20, 1000.0),), fill_factor=1.0, reject=None):
        self.asks = list(asks)
        self.fill_factor = fill_factor
        self.reject = reject
        self.buys = []

    def book(self, token_id):
        return {"asks": self.asks, "min_order_size": 5.0, "condition_id": f"c-{token_id}"}

    def buy(self, token_id, amount_usd, max_price, max_spend_usd):
        self.buys.append((token_id, amount_usd, max_price, max_spend_usd))
        if self.reject:
            return {"ok": False, "code": self.reject, "message": "no"}
        return {"ok": True, "status": "matched", "order_id": "o1",
                "spent_usd": max_spend_usd * self.fill_factor,
                "shares": amount_usd / max_price * self.fill_factor}


def priced(n_markets=1):
    fairs, books = [], []
    for i in range(n_markets):
        mid = f"m{i}"
        fairs.append(fair_row(market_id=mid))
        books += book_rows(market_id=mid, outcome_index=0, asks=[(0.20, 1000.0)])
    return {"fair": pd.DataFrame(fairs), "books": pd.DataFrame(books), "bars": pd.DataFrame()}


def run(s, mode, venue, ledger, p=None, **kw):
    p = p or priced()
    return trader.decide_and_trade(s, mode, CYCLE, p["fair"], p["books"], venue, ledger, NOW,
                                   echo=lambda *_: None, **kw)


def test_dry_run_never_buys(s, tmp_path):
    venue = FakeVenue()
    counts = run(s, "dry", venue, Ledger(tmp_path))
    assert counts["orders"] == 1 and venue.buys == []
    assert Ledger(tmp_path).fills("dry")[0]["spent_usd"] == pytest.approx(5.0)
    assert Ledger(tmp_path).fills("live") == []


def test_live_buys_fill_and_kill_within_the_market_cap(s, tmp_path):
    venue = FakeVenue()
    run(s, "live", venue, Ledger(tmp_path))
    (token, amount, max_price, max_spend), = venue.buys
    assert max_spend == pytest.approx(s.cap_per_market_usd)
    assert max_price == 0.20 and amount < max_spend   # price excludes the fee


def test_market_bought_once_ever(s, tmp_path):
    ledger = Ledger(tmp_path)
    run(s, "dry", FakeVenue(), ledger)
    counts = run(s, "dry", FakeVenue(), ledger)
    assert counts["orders"] == 0 and counts["skipped"] == {"already_held": 1}


def test_max_orders_per_cycle(s, tmp_path):
    s = replace(s, max_orders_per_cycle=2)
    counts = run(s, "dry", FakeVenue(), Ledger(tmp_path), p=priced(5))
    assert counts["orders"] == 2


def test_daily_cap_binds(s, tmp_path):
    s = replace(s, daily_spend_usd=12.0, max_orders_per_cycle=10)
    counts = run(s, "dry", FakeVenue(), Ledger(tmp_path), p=priced(5))
    assert counts["spent_usd"] == pytest.approx(12.0)
    assert counts["skipped"].get("no_room_daily") == 2


def test_bankroll_binds_across_days(s, tmp_path):
    s = replace(s, bankroll_usd=8.0, max_orders_per_cycle=10)
    counts = run(s, "dry", FakeVenue(), Ledger(tmp_path), p=priced(3))
    assert counts["spent_usd"] == pytest.approx(8.0)


def test_wallet_balance_binds_live(s, tmp_path):
    s = replace(s, max_orders_per_cycle=10)
    counts = run(s, "live", FakeVenue(), Ledger(tmp_path), p=priced(3), balance_usd=7.0)
    assert counts["spent_usd"] == pytest.approx(7.0)


def test_barrier_printed_since_snapshot_is_skipped(s, tmp_path):
    venue = FakeVenue()
    counts = run(s, "live", venue, Ledger(tmp_path), printed_since=lambda *a: True)
    assert venue.buys == [] and counts["skipped"] == {"printed_since_snapshot": 1}


def test_live_book_moved_past_the_limit(s, tmp_path):
    venue = FakeVenue(asks=[(0.29, 1000.0)])
    counts = run(s, "live", venue, Ledger(tmp_path))
    assert venue.buys == [] and counts["skipped"] == {"book_too_thin_or_moved": 1}


def test_rejected_order_is_recorded_and_not_held(s, tmp_path):
    ledger = Ledger(tmp_path)
    counts = run(s, "live", FakeVenue(reject="fak_not_filled"), ledger)
    assert counts["orders"] == 0 and ledger.held_markets("live") == set()
    assert "fak_not_filled" in (tmp_path / "orders.jsonl").read_text()


def test_fill_bigger_than_asked_halts(s, tmp_path, kill_switch):
    venue = FakeVenue(fill_factor=1e6)   # e.g. base units read as dollars
    counts = run(replace(s, max_orders_per_cycle=3), "live", venue, Ledger(tmp_path), p=priced(3))
    assert counts.get("halted") and kill_switch.exists()
    assert len(venue.buys) == 1


# ------------------------------------------------------------ independence


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


LIVE_FILES = [p for p in LIVE.rglob("*.py") if "tests" not in p.parts]
ENGINE = {p.stem for p in (LIVE / "pricing_engine").glob("*.py")}
COLLECTOR = {p.stem for p in (LIVE.parent / "data" / "src").glob("*.py")}


def test_live_never_reaches_into_model():
    """Checked at runtime, not by reading source: the engine copy's comments
    still name model/ files, which is history, not a dependency."""
    import sys

    import pricing
    model_dir = str(LIVE.parent / "model")
    assert not [p for p in sys.path if p.rstrip("/") == model_dir]
    for name in ENGINE:
        module = sys.modules.get(name)
        if module is not None:
            assert str(LIVE / "pricing_engine") in module.__file__, name
    assert pricing.data.__file__.startswith(str(LIVE / "pricing_engine"))


def test_only_pricing_imports_the_engine_or_the_collector():
    for path in LIVE.glob("*.py"):
        if path.name == "pricing.py":
            continue
        leaked = _imports(path) & (ENGINE | COLLECTOR)
        assert not leaked, f"{path.name} imports {leaked}: only pricing.py may"


def test_nothing_outside_live_imports_it():
    live_modules = {p.stem for p in LIVE.glob("*.py")}
    for folder in ("model", "data"):
        for path in (LIVE.parent / folder).rglob("*.py"):
            assert not (_imports(path) & live_modules), path


def test_only_venue_imports_the_sdk():
    for path in LIVE_FILES:
        if path.name != "venue.py":
            assert "polymarket" not in _imports(path), path.name


# ------------------------------------------------------------ live books


class BookVenue:
    def __init__(self, asks):
        self.asks = asks

    def books(self, token_ids):
        return {t: self.asks[t] for t in token_ids if t in self.asks}


def test_live_books_take_the_venue_s_prices_and_the_snapshot_s_names():
    snap = pd.DataFrame(book_rows(market_id="m1", outcome_index=0, asks=[(0.50, 10)])
                        + book_rows(market_id="m1", outcome_index=1, asks=[(0.60, 10)]))
    venue = BookVenue({"m1-0": [(0.20, 30.0), (0.25, 5.0)]})   # NO no longer quoted
    out = trader.live_books(venue, snap, ["m1"])
    assert out["price"].tolist() == [0.20, 0.25]
    assert out["depth_rank"].tolist() == [0, 1]
    assert set(out["ticker"]) == {"AAPL"} and set(out["outcome_index"]) == {0}
