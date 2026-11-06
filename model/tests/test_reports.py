"""The combined report: the summary table and the warning that has to sit
above a PnL measured on a window shorter than the markets; the Brier score.
"""
from __future__ import annotations

import pandas as pd
import pytest

import lab
from pipeline import config as configs
from reporting import results


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
    text = results.combined_report(rows, pd.DataFrame(), {"selection_biased": False},
                                   ["2026-09-16"], 1.0)
    assert "random_control" in text and "no expiry 3" in text


def test_a_window_with_no_settled_non_touch_is_labelled_as_such():
    """The warning is not decoration. A reader who takes the PnL column at
    face value here would conclude that selling longshots loses money, when
    what the window shows is that only the touched markets have settled."""
    sample = {"markets": 680, "resolved": 51, "deadline_passed": 0,
              "base_rate": 1.0, "selection_biased": True}
    text = results.headline_warning([row(settled=17)], sample)
    assert "has not landed" in text
    assert "0 have reached their" in text


def test_an_unbiased_window_gets_a_plain_statement_instead():
    sample = {"markets": 680, "resolved": 400, "deadline_passed": 380,
              "base_rate": 0.31, "selection_biased": False}
    text = results.headline_warning([row()], sample)
    assert "coin" not in text and "0.310" in text


def test_the_brier_score_reports_the_sample_beside_the_scores(synthetic_store):
    """`brier` returns `(scores, sample)` so no caller can print the table
    without having been handed the reason not to."""
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
    scores, sample = lab.brier(configs.load("mvp"), dates)
    assert sample["deadline_passed"] > 0 and not sample["selection_biased"]
    assert set(scores["estimate"]) == {"fair", "market_mid"}
    assert scores["brier"].between(0, 1).all()


def test_a_model_report_says_when_every_resolved_market_was_a_touch():
    note = results._selection_note({"positions_resolved": 17, "resolved_yes_share": 1.0,
                                "resolved_on_no": 14})
    assert "All 17" in note and "selection, not skill" in note


def test_a_mixed_resolved_sample_gets_the_share_and_no_sermon():
    note = results._selection_note({"positions_resolved": 40, "resolved_yes_share": 0.35,
                                "resolved_on_no": 20})
    assert "35%" in note and "selection" not in note
