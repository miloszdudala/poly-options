"""`run_all.py`: the summary table and the warning that has to sit above a
PnL measured on a window shorter than the markets.
"""
from __future__ import annotations

import pandas as pd
import pytest

import run_all
import results
import sim


def row(**kw):
    base = {"model": "m", "type": "t", "bets": 3, "money_in": 50.0, "promised_ev": 4.0,
            "settled": 2, "settled_promised_ev": 2.0, "pnl": 1.0,
            "realised_over_promised": 0.5, "periods_settled": 1}
    return {**base, **kw}


def test_the_summary_table_has_one_row_per_model_and_type_and_a_header():
    text = results.summary_table([row(model="a"), row(model="b")])
    assert text.count("\n") == 3          # header, rule, two rows
    assert "| a | t |" in text and "| b | t |" in text
    assert "promised (settled)" in text and "PnL (settled)" in text


def test_a_missing_number_prints_as_a_dash_rather_than_nan():
    text = results.summary_table([row(realised_over_promised=float("nan"))])
    assert "nan" not in text and "| - |" in text


def test_the_combined_report_names_the_control_and_the_skips():
    rows = [row(model="edge_taker"), row(model="random_control", skipped={"no expiry": 3})]
    text = run_all.combined_report(rows, pd.DataFrame(), {"selection_biased": False},
                                   ["2026-09-16"], 1.0)
    assert "random_control" in text and "no expiry 3" in text


def test_a_window_with_no_settled_non_touch_is_labelled_as_such():
    """The warning is not decoration. A reader who takes the PnL column at
    face value here would conclude that selling longshots loses money, when
    what the window shows is that only the touched markets have settled."""
    sample = {"markets": 680, "resolved": 51, "deadline_passed": 0,
              "base_rate": 1.0, "selection_biased": True}
    text = run_all.headline_warning([row(settled=17)], sample)
    assert "has not landed" in text
    assert "0 have reached their" in text


def test_an_unbiased_window_gets_a_plain_statement_instead():
    sample = {"markets": 680, "resolved": 400, "deadline_passed": 380,
              "base_rate": 0.31, "selection_biased": False}
    text = run_all.headline_warning([row()], sample)
    assert "coin" not in text and "0.310" in text


def test_universe_scores_reports_the_sample_beside_the_scores(monkeypatch):
    """`universe_scores` returns `(scores, sample)` precisely so that no caller
    can print the table without having been handed the reason not to."""
    fair = pd.DataFrame([{
        "cycle_id": "20260916T140000Z-a", "market_id": "m1", "ticker": "NVDA",
        "fair": 0.7,
        "deadline_utc": pd.Timestamp("2026-09-30T20:00:00Z")}])
    outcomes = pd.DataFrame([{"market_id": "m1", "result": "Yes",
                              "outcome_prices": None,
                              "settled_at_utc": pd.Timestamp("2026-09-17T18:00:00Z")}])
    monkeypatch.setattr(sim.data, "fair_values", lambda date: fair)
    monkeypatch.setattr(sim.data, "resolutions_upto", lambda when: outcomes)
    monkeypatch.setattr(results, "_market_mids", lambda dates: pd.DataFrame(
        [{"market_id": "m1", "cycle_id": "20260916T140000Z-a", "market_mid": 0.9}]))
    monkeypatch.setattr(results, "latest_settlement", lambda: "2026-09-18T00:00:00Z")

    scores, sample = results.universe_scores(["2026-09-16"])
    assert sample["deadline_passed"] == 0
    assert sample["selection_biased"] is True
    assert sample["base_rate"] == 1.0
    assert set(scores["estimate"]) == {"fair", "market_mid"}
    assert scores.set_index("estimate").loc["fair", "brier"] == pytest.approx(0.09)


def test_a_model_report_says_when_every_resolved_market_was_a_touch():
    note = results._selection_note({"positions_resolved": 17, "resolved_yes_share": 1.0,
                                "resolved_on_no": 14})
    assert "All 17" in note and "selection, not skill" in note


def test_a_mixed_resolved_sample_gets_the_share_and_no_sermon():
    note = results._selection_note({"positions_resolved": 40, "resolved_yes_share": 0.35,
                                "resolved_on_no": 20})
    assert "35%" in note and "selection" not in note
