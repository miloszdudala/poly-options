"""The dashboard renders from a small ledger, offline."""
from __future__ import annotations

import json
import re

import dashboard
import results
from test_sim import NEXT_MON, MON, TUE, asks, priced, replay, resolved


def fixture_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(results.ledger_schema, "STORE", tmp_path / "store")
    monkeypatch.setattr(results, "REPORTS", tmp_path / "reports")
    cycles = {MON: (asks(MON, "a", yes=((0.20, 100.0),)), [priced("a")]),
              TUE: (asks(TUE, "b", no=((0.30, 100.0),)), [priced("b", fair=0.50)]),
              NEXT_MON: (asks(NEXT_MON, "c", yes=((0.20, 100.0),)), [priced("c")])}
    results.write_run(replay(cycles, [resolved("a", "Yes"), resolved("b", "Yes")]))
    return tmp_path / "store" / "ledger"


def embedded(html: str) -> list[dict]:
    match = re.search(r"const BETS = (.*?);\n", html, re.S)
    return json.loads(match.group(1))


def test_it_renders_every_bet_from_the_ledger(tmp_path, monkeypatch):
    ledger = fixture_ledger(tmp_path, monkeypatch)
    out = dashboard.build(ledger, tmp_path / "dashboard.html")
    bets = embedded(out.read_text())
    assert sorted(b["market_id"] for b in bets) == ["a", "b", "c"]
    settled = {b["market_id"]: b["pnl_usd"] for b in bets}
    assert settled["a"] > 0 and settled["b"] < 0 and settled["c"] is None
    assert {b["run"] for b in bets} == {"take_all__fake"}


def test_it_needs_no_network(tmp_path, monkeypatch):
    html = dashboard.build(fixture_ledger(tmp_path, monkeypatch),
                           tmp_path / "dashboard.html").read_text()
    assert not re.search(r"""(src|href)=["']?https?:""", html)
    assert "<script src" not in html and "@import" not in html


def test_the_page_has_the_tables_and_filters_the_plan_asks_for(tmp_path, monkeypatch):
    html = dashboard.build(fixture_ledger(tmp_path, monkeypatch),
                           tmp_path / "dashboard.html").read_text()
    for needle in ('id="f-model"', 'id="f-type"', 'id="f-from"', 'id="f-to"',
                   "promised (settled)", "PnL (settled)", "realised / promised",
                   "settled periods", "By period of entry", "betRows"):
        assert needle in html, needle


def test_an_empty_store_still_renders(tmp_path):
    html = dashboard.build(tmp_path / "nothing", tmp_path / "dashboard.html").read_text()
    assert embedded(html) == []
