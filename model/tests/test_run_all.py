"""`run_all.py`: the summary table, the best/worst-day split, and the warning
that has to sit above a PnL measured on a window shorter than the markets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import run_all
import sim


def row(**kw):
    base = {"model": "m", "positions_opened": 3, "wanted_usd": 100.0,
            "filled_usd": 50.0, "cost_confirmed_usd": 25.0, "positions_resolved": 2,
            "pnl_usd": 1.0, "pnl_confirmed_usd": 0.5, "open_pnl_at_fair_usd": 0.0,
            "open_pnl_at_bid_usd": -1.0, "bdd_max_abs_frac": 0.1,
            "largest_ticker_share": 0.5, "worst_day_usd": -2.0}
    return {**base, **kw}


def test_the_summary_table_has_one_row_per_model_and_a_header():
    text = run_all.summary_table([row(model="a"), row(model="b")])
    assert text.count("\n") == 3          # header, rule, two rows
    assert "| a |" in text and "| b |" in text


def test_a_missing_number_prints_as_a_dash_rather_than_nan():
    text = run_all.summary_table([row(pnl_usd=float("nan"))])
    assert "nan" not in text and "| - |" in text


def test_the_best_and_worst_spy_days_come_from_the_days_with_a_spy_return():
    daily = pd.DataFrame({
        "pnl_marked_usd": [10.0, -50.0, 5.0],
        "spy_return": [0.01, -0.04, float("nan")],
    }, index=["2026-09-14", "2026-09-15", "2026-09-16"])
    out = run_all.best_and_worst_days(daily)
    assert out["worst_spy_date"] == "2026-09-15"
    assert out["pnl_on_worst_spy_day"] == -50.0
    assert out["best_spy_date"] == "2026-09-14"
    assert out["spy_days"] == 2


def test_the_worst_day_is_the_worst_pnl_even_without_a_spy_return():
    daily = pd.DataFrame({"pnl_marked_usd": [10.0, -99.0],
                          "spy_return": [float("nan")] * 2},
                         index=["2026-09-15", "2026-09-16"])
    out = run_all.best_and_worst_days(daily)
    assert out["worst_day_usd"] == -99.0
    assert "worst_spy_date" not in out


def test_the_day_count_does_not_overwrite_the_one_metrics_owns():
    """`metrics['days']` is session days replayed; this index is wider because
    it includes days on which something only settled. Naming both `days` made
    a one-day run report itself as two."""
    daily = pd.DataFrame({"pnl_marked_usd": [1.0, 2.0], "spy_return": [0.0, 0.0]},
                         index=["2026-09-16", "2026-09-17"])
    out = run_all.best_and_worst_days(daily)
    assert "days" not in out
    assert out["pnl_days"] == 2


def test_a_window_with_no_settled_non_touch_is_labelled_as_such():
    """The warning is not decoration. A reader who takes the PnL column at
    face value here would conclude that selling longshots loses money, when
    what the window shows is that only the touched markets have settled."""
    sample = {"markets": 680, "resolved": 51, "deadline_passed": 0,
              "base_rate": 1.0, "selection_biased": True}
    text = run_all._headline_warning([row(positions_resolved=17)], sample)
    assert "has not landed" in text
    assert "0 have reached their" in text


def test_an_unbiased_window_gets_a_plain_statement_instead():
    sample = {"markets": 680, "resolved": 400, "deadline_passed": 380,
              "base_rate": 0.31, "selection_biased": False}
    text = run_all._headline_warning([row()], sample)
    assert "coin" not in text and "0.310" in text


def test_universe_scores_reports_the_sample_beside_the_scores(monkeypatch):
    """`universe_scores` returns `(scores, sample)` precisely so that no caller
    can print the table without having been handed the reason not to."""
    fair = pd.DataFrame([{
        "cycle_id": "20260916T140000Z-a", "market_id": "m1", "ticker": "NVDA",
        "fair_K": 0.7, "fair_atm": 0.7, "fair_cont": 0.8, "fair_empirical": 0.6,
        "deadline_utc": pd.Timestamp("2026-09-30T20:00:00Z")}])
    outcomes = pd.DataFrame([{"market_id": "m1", "result": "Yes",
                              "outcome_prices": None,
                              "settled_at_utc": pd.Timestamp("2026-09-17T18:00:00Z")}])
    monkeypatch.setattr(sim.data, "fair_values", lambda date: fair)
    monkeypatch.setattr(sim.data, "resolutions_upto", lambda when: outcomes)
    monkeypatch.setattr(sim, "_market_mids", lambda dates: pd.DataFrame(
        [{"market_id": "m1", "cycle_id": "20260916T140000Z-a", "market_mid": 0.9}]))
    monkeypatch.setattr(sim, "_latest_settlement", lambda: "2026-09-18T00:00:00Z")

    scores, sample = sim.universe_scores(["2026-09-16"])
    assert sample["deadline_passed"] == 0
    assert sample["selection_biased"] is True
    assert sample["base_rate"] == 1.0
    assert set(scores["estimate"]) == {"fair_K", "fair_atm", "fair_cont",
                                       "fair_empirical", "market_mid"}
    assert scores.set_index("estimate").loc["fair_K", "brier"] == pytest.approx(0.09)


def test_a_model_report_says_when_every_resolved_market_was_a_touch():
    note = sim._selection_note({"positions_resolved": 17, "resolved_yes_share": 1.0,
                                "resolved_on_no": 14})
    assert "All 17" in note and "selection, not skill" in note


def test_a_mixed_resolved_sample_gets_the_share_and_no_sermon():
    note = sim._selection_note({"positions_resolved": 40, "resolved_yes_share": 0.35,
                                "resolved_on_no": 20})
    assert "35%" in note and "selection" not in note


def test_every_model_run_all_builds_carries_its_own_params():
    """MODELS 5.1-5.3 give three different sets of caps; a model that inherited
    the defaults would be measuring a different strategy than the one named."""
    models = run_all.build_models()
    assert [m.name for m in models] == ["edge_taker", "factor_neutral",
                                        "balanced_book", "direction_balanced",
                                        "scenario_hedged", "empirical_longshot"]
    assert len({(m.params.ticker_cap_frac, m.params.direction_cap_frac)
                for m in models}) == len(models)


def test_model_d_the_maker_is_not_built():
    """MODELS 5.4 defers it, and the prompt says not to build it. Pinned so a
    later pass has to delete this test on purpose rather than by accident."""
    assert all("maker" not in m.name for m in run_all.build_models())


def test_a_tagged_threshold_sweep_gets_its_own_names():
    """So a month-end re-run at a 10c bar does not overwrite the 3c run."""
    models = run_all.build_models(min_ev=0.10, min_roi=0.20, tag="ev10")
    assert [m.name for m in models] == ["edge_taker_ev10", "factor_neutral_ev10",
                                        "balanced_book_ev10", "direction_balanced_ev10",
                                        "scenario_hedged_ev10", "empirical_longshot_ev10"]
    assert models[0].min_ev == 0.10 and models[1].min_roi == 0.20
    assert run_all.build_models()[0].name == "edge_taker"
