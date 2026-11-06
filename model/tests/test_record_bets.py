"""BETS.md: rows are only added, on main, whatever is checked out."""
from __future__ import annotations

import subprocess

import pandas as pd
import pytest

import record_bets as rb
import replay

HEADER = "# Bets\n\n| Written | Book | Deadline | Market ID | Market |\n|---|---|---|---|---|\n"


def _position():
    when = pd.Timestamp("2026-11-06T15:00:00Z")
    p = replay.Position(market_id="m1", side="NO", ticker="META", contract_type="hit_up",
                        level=640.0, deadline=when, period="weekly", direction="down", vol="calm",
                        entry_cycle_id="c", entry_at=when, win_prob=0.9, shares=10.0,
                        cost_usd=8.0, ev_usd=1.0)
    p.fills.append(replay.Fill("c", when, 0, 0.8, 10.0, 0.0, 8.0, 1.0, 20.0))
    return p


def test_a_written_row_gives_back_its_market_id():
    row = rb.table_row(_position(), pd.Timestamp("2026-11-06T15:05:00Z"), "abc", "Hit $640?")
    assert rb.market_ids(HEADER + row) == {"m1"}
    assert "2026-11-06 15:05:00" in row and "| NO |" in row and "| 90.0% |" in row


def test_a_pipe_in_a_question_cannot_shift_the_columns():
    row = rb.table_row(_position(), pd.Timestamp("2026-11-06T15:05:00Z"), "abc", "a | b")
    assert rb.market_ids(row) == {"m1"}


def test_the_header_is_not_read_as_a_market():
    assert rb.market_ids(HEADER) == set()


@pytest.fixture
def repo(tmp_path):
    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (tmp_path / rb.BETS).write_text(HEADER)
    run("add", rb.BETS)
    run("commit", "-q", "-m", "start")
    return tmp_path


def test_appending_on_main_from_main_keeps_the_checkout_clean(repo):
    rb.append_to_main(repo, "| 2026 | row |\n", "one")
    assert (repo / rb.BETS).read_text() == HEADER + "| 2026 | row |\n"
    assert rb.git(repo, "status", "--porcelain") == ""


def test_appending_leaves_another_branch_and_its_staged_work_alone(repo):
    rb.git(repo, "checkout", "-q", "-b", "work")
    (repo / "other.txt").write_text("staged")
    rb.git(repo, "add", "other.txt")
    rb.append_to_main(repo, "| 2026 | row |\n", "one")
    rb.append_to_main(repo, "| 2026 | two |\n", "two")
    assert rb.read_main(repo) == HEADER + "| 2026 | row |\n| 2026 | two |\n"
    assert (repo / rb.BETS).read_text() == HEADER
    assert rb.git(repo, "branch", "--show-current") == "work"
    assert rb.git(repo, "diff", "--cached", "--name-only") == "other.txt"


def test_a_ticker_with_stale_bars_is_behind():
    stamp = pd.Timestamp("2026-11-06T15:00:00Z")
    today = pd.DataFrame({"symbol": ["META", "AAPL"],
                          "bar_time": [stamp - pd.Timedelta(minutes=5),
                                       stamp - pd.Timedelta(minutes=30)]})
    assert rb.tickers_behind(today, ["META", "AAPL", "MSFT"], stamp) == ["AAPL", "MSFT"]


def test_todays_bars_win_an_overlap_with_the_store():
    when = pd.Timestamp("2026-11-06T15:00:00Z")
    stored = pd.DataFrame({"symbol": ["META"], "bar_time": [when], "open": [1.0]})
    today = pd.DataFrame({"symbol": ["META"], "bar_time": [when], "open": [2.0]})
    assert rb.bars_with_today(stored, today)["open"].tolist() == [2.0]
