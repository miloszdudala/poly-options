"""The bets record: rows are only added, and the book is rebuilt from them alone."""
from __future__ import annotations

import pandas as pd
import pytest

import record_bets as rb
import sim


def _row(market_id="m1", side="NO", cost="100.0"):
    return {"market_id": market_id, "side": side, "ticker": "META", "kind": "hit_up",
            "barrier": "800", "deadline_utc": "2026-11-13T21:00:00+00:00",
            "cycle_id": "20261106T150005Z-aaaaaa",
            "book_seen_at_utc": "2026-11-06T15:00:10+00:00", "fair_source": "x",
            "shares": "120", "cost_usd": cost, "fee_usd": "1.0"}


def test_appending_keeps_every_earlier_row_and_one_header(tmp_path):
    path = tmp_path / "bets" / "2026-11-06.csv"
    rb.append_rows(path, ["a", "b"], [{"a": 1, "b": 2}])
    first = path.read_text()
    rb.append_rows(path, ["a", "b"], [{"a": 3, "b": 4}])
    assert path.read_text().startswith(first)
    assert rb.read_rows(path) == [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]


def test_nothing_to_append_creates_no_file(tmp_path):
    path = tmp_path / "bets" / "x.csv"
    rb.append_rows(path, ["a"], [])
    assert not path.exists()


def test_book_from_record_holds_the_bets_and_spends_the_cash():
    book = rb.book_from_record([_row(), _row("m2", "YES", "50.0")], sim.Params())
    assert book.cash == pytest.approx(9_850.0)
    assert set(book.open) == {("m1", sim.NO), ("m2", sim.YES)}
    assert book.open[("m1", sim.NO)].shares == 120.0


def test_a_held_market_is_not_bought_again():
    book = rb.book_from_record([_row()], sim.Params())
    assert book.held_direction("m1") == sim.NO


def test_a_ticker_with_stale_bars_is_left_out():
    stamp = pd.Timestamp("2026-11-06T15:00:00Z")
    today = pd.DataFrame({"symbol": ["META", "AAPL"],
                          "bar_time": [stamp - pd.Timedelta(minutes=5),
                                       stamp - pd.Timedelta(minutes=30)]})
    assert rb.tickers_covered(today, ["META", "AAPL", "MSFT"], stamp) == ["META"]


def test_todays_bars_win_an_overlap_with_the_store():
    when = pd.Timestamp("2026-11-06T15:00:00Z")
    stored = pd.DataFrame({"symbol": ["META"], "bar_time": [when], "open": [1.0]})
    today = pd.DataFrame({"symbol": ["META"], "bar_time": [when], "open": [2.0]})
    assert rb.bars_with_today(stored, today)["open"].tolist() == [2.0]
