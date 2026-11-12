"""proof/: rows are only added, on main, whatever is checked out."""
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
    (tmp_path / "proof").mkdir()
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


BET = ["2026-11-06 15:53:55", "2026-11-06 15:45:20", "2026-11-06 21:00:00", "m1", "Hit $640?",
       "NO", "37.50", "0.740", "28.14", "89.4%", "5.39", "abc"]


def _resolved(prices, status="resolved"):
    return {"umaResolutionStatus": status, "outcomes": '["Yes", "No"]', "outcomePrices": prices,
            "closedTime": "2026-11-06 21:04:06.36125+00"}


WRITTEN = pd.Timestamp("2026-11-06T22:00:00Z")


def _cells(row):
    return rb.table_rows(row)[0]


def test_a_no_that_won_is_paid_a_dollar_a_share():
    cells = _cells(rb.settlement_row(BET, _resolved('["0", "1"]'), WRITTEN))
    assert cells == ["2026-11-06 22:00:00", "2026-11-06 21:04:06", "m1", "No", "37.50", "9.36"]


def test_a_no_that_lost_loses_its_cost():
    assert _cells(rb.settlement_row(BET, _resolved('["1", "0"]'), WRITTEN))[-3:] == ["Yes", "0.00", "-28.14"]


def test_a_yes_is_paid_by_the_first_outcome():
    yes = BET[:5] + ["YES"] + BET[6:]
    assert _cells(rb.settlement_row(yes, _resolved('["1", "0"]'), WRITTEN))[-3:] == ["Yes", "37.50", "9.36"]


def test_a_fifty_fifty_pays_half():
    assert _cells(rb.settlement_row(BET, _resolved('["0.5", "0.5"]'), WRITTEN))[-3:] == ["50-50", "18.75", "-9.39"]


@pytest.mark.parametrize("market", [_resolved('["0.6", "0.4"]'), _resolved('["1", "0"]', status="disputed"),
                                    _resolved('["1", "0"]', status=None)])
def test_an_unresolved_or_disputed_market_gets_no_row(market):
    assert rb.settlement_row(BET, market, WRITTEN) is None


def test_a_bet_stays_unsettled_only_until_its_row_exists():
    bets = HEADER + "| " + " | ".join(BET) + " |\n"
    assert rb.unsettled(bets, HEADER) == [BET]
    assert rb.unsettled(bets, HEADER + rb.settlement_row(BET, _resolved('["0", "1"]'), WRITTEN)) == []


def test_settlements_append_to_their_own_file(repo):
    (repo / rb.SETTLEMENTS).write_text("settlements\n")
    rb.git(repo, "add", rb.SETTLEMENTS)
    rb.git(repo, "commit", "-q", "-m", "more")
    rb.append_to_main(repo, "| 2026 | row |\n", "one", rb.SETTLEMENTS)
    assert (repo / rb.SETTLEMENTS).read_text() == "settlements\n| 2026 | row |\n"
    assert rb.read_main(repo) == HEADER
    assert rb.git(repo, "status", "--porcelain") == ""


def test_the_review_sets_settled_pnl_against_promised_ev(tmp_path):
    import review_proof
    row = "| " + " | ".join(BET) + " |\n"
    other = row.replace("| m1 |", "| m2 |")
    (tmp_path / "BETS.md").write_text(HEADER + row + other)
    (tmp_path / "SETTLEMENTS.md").write_text(
        HEADER + rb.settlement_row(BET, _resolved('["0", "1"]'), WRITTEN)
        + "| 2026-11-06 22:00:00 | 2026-11-06 21:00:00 | m9 | No | 1.00 | 0.50 |\n")
    text = review_proof.review(tmp_path)
    assert "2026-11-06     1     1      28.14        5.39       9.36          1.74" in text
    assert "1 bet(s) open: $28.14 in, $5.39 promised" in text
    assert "settlements with no bet: m9" in text



def _week():
    bet = list(BET)
    bet[4] = "META hit $640 Week of November 2 2026?"
    first = "| " + " | ".join(bet) + " |\n"
    settled = HEADER + rb.settlement_row(bet, _resolved('["0", "1"]'), WRITTEN)
    both = settled + settled.splitlines(keepends=True)[-1].replace("| m1 |", "| m2 |")
    return HEADER + first + first.replace("| m1 |", "| m2 |"), settled, both


AFTER = WRITTEN.to_pydatetime()


def test_a_week_waits_for_its_last_settlement():
    bets, settled, both = _week()
    assert rb.performance.table(bets, settled, AFTER) == rb.performance.HEADER
    row = rb.performance.table(bets, both, AFTER)
    assert ("| 2026-11-06 22:00:00 | 2026-11-06 21:00:00 | 2 | 2 | 0 | 1.8 | 56.28 | 10.78 "
            "| 18.72 | 1.74 | 33.3% |") in row


def test_a_week_waits_for_its_deadline():
    bets, _, both = _week()
    before = WRITTEN.replace(hour=20).to_pydatetime()
    assert rb.performance.table(bets, both, before) == rb.performance.HEADER


def test_only_weekly_markets_make_a_week():
    bets, _, both = _week()
    monthly = bets.replace("Week of November 2 2026", "in November")
    assert rb.performance.table(monthly, both, AFTER) == rb.performance.HEADER


def test_performance_md_starts_on_main_and_only_grows(repo, monkeypatch):
    bets, _, both = _week()
    (repo / rb.SETTLEMENTS).write_text(both)
    (repo / rb.BETS).write_text(bets)
    rb.git(repo, "add", rb.BETS, rb.SETTLEMENTS)
    rb.git(repo, "commit", "-q", "-m", "bets")
    monkeypatch.setattr(rb, "REPO", repo)
    rb.record_performance()
    rb.record_performance()
    text = (repo / rb.PERFORMANCE).read_text()
    assert text.startswith(rb.performance.HEADER) and text.count("| 2026-11-06 21:00:00 |") == 1
    assert rb.git(repo, "status", "--porcelain") == ""
    assert rb.git(repo, "log", "--format=%s", "-1") == "1 week(s) of performance"
