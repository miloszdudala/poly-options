"""`daily.py`: the incremental pricing step, today's slices, the collector's
gaps, and the launchd wiring that would otherwise fail silently at 18:00.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import daily
import sim
from sim import NO, YES, Params

REPO = Path(__file__).resolve().parent.parent.parent
SCHEDULING = REPO / "scheduling"


# ------------------------------------------------------- 1. price the gaps


@pytest.fixture(autouse=True)
def _bars_present(request, monkeypatch):
    """The fake cycle ids below have no clock; the bar guard has its own tests."""
    if "bar_guard" not in request.node.name:
        monkeypatch.setattr(daily, "bars_cover", lambda cycle_id: True)


def test_only_the_cycles_that_are_missing_are_priced(monkeypatch):
    monkeypatch.setattr(daily.data, "session_cycles",
                        lambda date: ["c1", "c2", "c3"])
    monkeypatch.setattr(daily.data, "fair_values",
                        lambda date: pd.DataFrame({"cycle_id": ["c1", "c3"]}))
    assert daily.unpriced_cycles(["2026-09-16"]) == {"2026-09-16": ["c2"]}


def test_a_fully_priced_date_does_not_appear_at_all(monkeypatch):
    monkeypatch.setattr(daily.data, "session_cycles", lambda date: ["c1"])
    monkeypatch.setattr(daily.data, "fair_values",
                        lambda date: pd.DataFrame({"cycle_id": ["c1"]}))
    assert daily.unpriced_cycles(["2026-09-16"]) == {}


def test_a_date_with_nothing_priced_yet_needs_all_of_it(monkeypatch):
    monkeypatch.setattr(daily.data, "session_cycles", lambda date: ["c1", "c2"])
    monkeypatch.setattr(daily.data, "fair_values", lambda date: pd.DataFrame())
    assert daily.unpriced_cycles(["2026-09-16"]) == {"2026-09-16": ["c1", "c2"]}


def _spy_bars_until(stamp):
    return pd.DataFrame({"symbol": ["SPY"], "bar_time": [pd.Timestamp(stamp, tz="UTC")]})


def test_bar_guard_defers_a_cycle_the_bars_have_not_reached(monkeypatch):
    """10-01: the collector's outage left no 5-minute bars at report time, the
    touch check saw no evidence, and fills landed on barriers already printed."""
    monkeypatch.setattr(daily.data, "bars_5m", lambda: _spy_bars_until("2026-10-01 13:55"))
    monkeypatch.setattr(daily.data, "session_cycles",
                        lambda date: ["20261001T140000Z-a", "20261001T141106Z-b"])
    monkeypatch.setattr(daily.data, "fair_values", lambda date: pd.DataFrame())
    lines = []
    assert daily.unpriced_cycles(["2026-10-01"], echo=lines.append) == {
        "2026-10-01": ["20261001T140000Z-a"]}
    assert "deferred 1 cycle(s)" in lines[0]


def test_bar_guard_passes_a_cycle_inside_the_newest_bar(monkeypatch):
    monkeypatch.setattr(daily.data, "bars_5m", lambda: _spy_bars_until("2026-10-01 19:55"))
    assert daily.bars_cover("20261001T200001Z-a")
    monkeypatch.setattr(daily.data, "bars_5m", lambda: _spy_bars_until("2026-10-01 19:45"))
    assert not daily.bars_cover("20261001T200001Z-a")


def test_bar_guard_defers_everything_with_no_bars_at_all(monkeypatch):
    monkeypatch.setattr(daily.data, "bars_5m",
                        lambda: pd.DataFrame({"symbol": [], "bar_time": []}))
    assert not daily.bars_cover("20261001T140000Z-a")


def test_price_missing_writes_one_partition_entry_per_missing_cycle(monkeypatch):
    """Appends rather than rewriting the partition, which is only safe because
    the cycles written are exactly the ones that were absent."""
    written = []
    monkeypatch.setattr(daily, "unpriced_cycles",
                        lambda dates, echo=None: {"2026-09-16": ["c2"]})
    monkeypatch.setattr(daily, "price_cycle",
                        lambda cycle_id: (pd.DataFrame([{"market_id": "m"}]),
                                          {"markets": 1, "priced": 1, "seconds": 0.1}))
    monkeypatch.setattr(daily.fair_schema, "write_fair",
                        lambda rows, date: written.append((date, len(rows))))
    stats = daily.price_missing(["2026-09-16"])
    assert written == [("2026-09-16", 1)]
    assert [s["priced"] for s in stats] == [1]


def test_an_empty_pricing_result_is_not_written(monkeypatch):
    """A cycle whose markets all failed to price has no rows; writing an empty
    part file would leave a partition entry that claims the cycle is done."""
    written = []
    monkeypatch.setattr(daily, "unpriced_cycles", lambda dates, echo=None: {"2026-09-16": ["c2"]})
    monkeypatch.setattr(daily, "price_cycle",
                        lambda cycle_id: (pd.DataFrame(),
                                          {"markets": 0, "priced": 0, "seconds": 0.0}))
    monkeypatch.setattr(daily.fair_schema, "write_fair",
                        lambda rows, date: written.append(date))
    daily.price_missing(["2026-09-16"])
    assert written == []


# ------------------------------------------------------- 2. today's slices


def result_with(capacity=None, equity=None, fills=None, settlements=None,
                cycles=("20260916T140000Z-a",)):
    return sim.Result(
        model="m", params=Params(), run_id="r", cycles=list(cycles),
        fills=fills or [], capacity=capacity if capacity is not None else pd.DataFrame(),
        marks=pd.DataFrame(), equity=equity if equity is not None else pd.DataFrame(),
        settlements=settlements or [], open_positions=[], portfolio=sim.Portfolio(Params()),
        seconds=1.0, settled_through="2026-09-18T00:00:00Z")


def test_todays_capacity_counts_only_todays_cycles():
    capacity = pd.DataFrame([
        {"cycle_id": "20260916T140000Z-a", "wanted_usd": 100.0, "filled_usd": 10.0},
        {"cycle_id": "20260917T140000Z-b", "wanted_usd": 900.0, "filled_usd": 90.0},
    ])
    out = daily.today_capacity(result_with(capacity=capacity), "2026-09-16")
    assert out == {"orders": 1, "wanted_usd": 100.0, "filled_usd": 10.0}


def test_todays_exposure_closes_on_the_last_cycle_and_peaks_on_the_largest():
    """The close is what MODELS 5.2's band is a statement about; the peak is
    what says whether the band held during the session. For `factor_neutral`
    on 2026-09-16 those differ by a factor of five."""
    equity = pd.DataFrame([
        {"date": "2026-09-16", "beta_dollar_delta": -200.0},
        {"date": "2026-09-16", "beta_dollar_delta": -991.0},
        {"date": "2026-09-16", "beta_dollar_delta": -212.0},
        {"date": "2026-09-17", "beta_dollar_delta": 5_000.0},
    ])
    out = daily.today_exposure(result_with(equity=equity), "2026-09-16")
    assert out["bdd_close"] == -212.0
    assert out["bdd_peak"] == -991.0
    assert out["cycles"] == 3


def test_a_date_the_model_did_not_trade_reports_nan_not_zero():
    """Zero would read as "flat and hedged"; there was no book at all."""
    equity = pd.DataFrame([{"date": "2026-09-17", "beta_dollar_delta": 5_000.0}])
    out = daily.today_exposure(result_with(equity=equity), "2026-09-16")
    assert np.isnan(out["bdd_close"]) and out["cycles"] == 0


def fill(cycle_id, cost, confirmed):
    return sim.Fill(cycle_id=cycle_id, filled_at=pd.Timestamp("2026-09-16T14:00:00Z"),
                    market_id="m1", outcome_index=YES, depth_rank=0, price=0.3,
                    shares=1.0, fee_usd=0.0, cost_usd=cost, level_size=10.0,
                    limit_price=0.5, fair=0.4, fair_source="", reason="t",
                    confirmed=confirmed, checked=True)


def test_todays_fills_count_only_today_and_only_confirmed_dollars():
    fills = [fill("20260916T140000Z-a", 10.0, True),
             fill("20260916T141500Z-b", 5.0, False),
             fill("20260917T140000Z-c", 99.0, True)]
    out = daily.today_fills(result_with(fills=fills), "2026-09-16")
    assert out == {"fills": 2, "confirmed_usd": 10.0}


# ------------------------------------------------------ 3. collector gaps


def runs_frame(rows):
    frame = pd.DataFrame(rows)
    frame["started_at_utc"] = pd.to_datetime(frame["started_at_utc"], utc=True)
    return frame


def test_a_skipped_tick_shows_up_as_a_gap_over_twice_the_median(monkeypatch):
    """Measured against the date's own median, not against 15 minutes: a
    snapshot that takes three minutes shifts every later tick and a fixed grid
    would call that a gap."""
    monkeypatch.setattr(daily.data, "runs", lambda since: runs_frame([
        {"date": "2026-09-16", "job": "snapshot", "in_session": True,
         "started_at_utc": f"2026-09-16T{h}:{m}:00Z", "duration_seconds": 100.0,
         "status": "ok", "failure_count": 0, "missing": 0.0}
        for h, m in (("14", "00"), ("14", "18"), ("14", "36"), ("15", "30"))]))
    out = daily.collector_gaps("2026-09-09")
    row = out.iloc[0]
    assert row["median_gap_min"] == pytest.approx(18.0)
    assert row["longest_gap_min"] == pytest.approx(54.0)
    assert row["gaps_over_2x"] == 1
    assert row["in_session_cycles"] == 4


def test_a_run_longer_than_one_cadence_is_counted_as_over_cadence(monkeypatch):
    """That is what a truncated cycle looks like from here: the 2026-09-17
    snapshots ran 3,409 s and 7,559 s against a 900 s tick."""
    monkeypatch.setattr(daily.data, "runs", lambda since: runs_frame([
        {"date": "2026-09-17", "job": "snapshot", "in_session": False,
         "started_at_utc": "2026-09-17T15:40:00Z", "duration_seconds": 6262.8,
         "status": "ok", "failure_count": 0, "missing": 102.0},
        {"date": "2026-09-17", "job": "snapshot", "in_session": False,
         "started_at_utc": "2026-09-17T14:15:00Z", "duration_seconds": 613.6,
         "status": "truncated", "failure_count": 2530, "missing": 2480.0}]))
    row = daily.collector_gaps("2026-09-10").iloc[0]
    assert row["over_cadence"] == 1
    assert row["not_ok"] == 1
    assert row["failures"] == 2530
    assert row["tokens_missing"] == 2582
    assert row["in_session_cycles"] == 0


def test_a_date_with_one_run_has_no_gap_to_measure(monkeypatch):
    monkeypatch.setattr(daily.data, "runs", lambda since: runs_frame([
        {"date": "2026-09-18", "job": "snapshot", "in_session": True,
         "started_at_utc": "2026-09-18T14:00:00Z", "duration_seconds": 40.0,
         "status": "ok", "failure_count": 0, "missing": 0.0}]))
    row = daily.collector_gaps("2026-09-11").iloc[0]
    assert np.isnan(row["median_gap_min"])
    assert row["gaps_over_2x"] == 0


def test_no_runs_at_all_gives_an_empty_frame_not_an_error(monkeypatch):
    monkeypatch.setattr(daily.data, "runs", lambda since: pd.DataFrame())
    assert daily.collector_gaps("2026-09-11").empty


# ------------------------------------------------------------ the date


def test_todays_date_is_used_when_today_was_traded(monkeypatch):
    monkeypatch.setattr(daily.dt, "datetime", _FixedNow("2026-09-16"))
    date, note = daily.resolve_date(None, ["2026-09-15", "2026-09-16"])
    assert date == "2026-09-16" and note is None


def test_a_day_with_no_session_falls_back_and_says_so(monkeypatch):
    """A weekend, a holiday, or a day the collector missed. The fallback keeps
    the report readable and the collector table below it shows why."""
    monkeypatch.setattr(daily.dt, "datetime", _FixedNow("2026-09-19"))
    date, note = daily.resolve_date(None, ["2026-09-16"])
    assert date == "2026-09-16"
    assert "2026-09-19 has no priced session cycle" in note


def test_an_explicit_date_is_never_second_guessed():
    assert daily.resolve_date("2026-01-01", ["2026-09-16"]) == ("2026-01-01", None)


class _FixedNow:
    def __init__(self, date):
        self._date = date

    def now(self, tz=None):
        return pd.Timestamp(self._date, tz="UTC").to_pydatetime()


# ----------------------------------------------------------- the report


def settlement(win_prob, won, cost=100.0):
    position = sim.Position(
        market_id=f"m{win_prob}{won}", outcome_index=YES, ticker="NVDA",
        contract_type="hit_up", barrier=1.0,
        deadline=pd.Timestamp("2026-09-18T20:00:00Z"), period="weekly",
        entry_cycle_id="20260916T140000Z-a",
        entry_at=pd.Timestamp("2026-09-16T14:00:00Z"), entry_fair=win_prob,
        shares=10.0, cost_usd=cost)
    payout = 1.0 if won else 0.0
    return sim.Settlement(
        position=position, result="Yes" if won else "No",
        settled_at=pd.Timestamp("2026-09-17T18:00:00Z"),
        cash_back_at=pd.Timestamp("2026-09-18T20:00:00Z"),
        payout_per_share=payout, payout_usd=payout * 10.0,
        pnl_usd=payout * 10.0 - cost, payout_confirmed_usd=0.0,
        pnl_confirmed_usd=-cost, capital_days=cost * 2.25)


def busy_run(name):
    settlements = [settlement(p, w) for p, w in
                   ((0.07, False), (0.15, False), (0.3, True), (0.45, True),
                    (0.6, True), (0.7, False), (0.85, True), (0.97, True))]
    result = result_with(
        capacity=pd.DataFrame([{"cycle_id": "20260916T140000Z-a", "wanted_usd": 100.0,
                                "budget_usd": 50.0, "filled_usd": 10.0,
                                "blocker": "book"}]),
        equity=pd.DataFrame([{"cycle_id": "20260916T140000Z-a", "date": "2026-09-16",
                              "beta_dollar_delta": -500.0, "equity_fair_usd": 9_900.0,
                              "equity_bid_usd": 9_800.0}]),
        settlements=settlements)
    m = sim.metrics(result)
    m["model"] = name
    m["worst_day_usd"] = -12.0
    return result, m


def test_the_report_stays_under_a_hundred_and_twenty_lines():
    """The prompt's ceiling, and the reason the reliability tables are one
    table with a model column rather than five tables."""
    runs = [busy_run(n) for n in ("edge_taker", "factor_neutral", "balanced_book",
                                  "empirical_longshot", "random_control")]
    gaps = pd.DataFrame([{"date": f"2026-09-{d:02d}", "in_session_cycles": 21,
                          "all_runs": 28, "median_gap_min": 17.5,
                          "longest_gap_min": 41.2, "gaps_over_2x": 1,
                          "over_cadence": 2, "not_ok": 4, "failures": 10,
                          "tokens_missing": 1282} for d in range(11, 19)])
    text = daily.report("2026-09-16", runs, ["2026-09-16"], [], gaps,
                        {"selection_biased": True, "resolved": 51, "markets": 680,
                         "deadline_passed": 0, "base_rate": 1.0}, 13.0)
    assert len(text.splitlines()) < 120


def test_the_report_leads_with_the_selection_warning_when_the_sample_is_touches():
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], [], pd.DataFrame(),
                        {"selection_biased": True, "resolved": 51, "markets": 680,
                         "deadline_passed": 0, "base_rate": 1.0}, 1.0)
    warning = text.index("PnL is not yet a result")
    assert warning < text.index("## Today")


def test_the_fallback_date_note_reaches_the_report_not_just_the_terminal():
    """The report is the artifact; a reader opening `daily_2026-09-16.md` on
    the 18th has to be told it is not the 18th."""
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], [], pd.DataFrame(),
                        {"selection_biased": False}, 1.0,
                        note="2026-09-18 has no priced session cycle; showing 2026-09-16")
    assert "**2026-09-18 has no priced session cycle" in text.splitlines()[2]


def test_an_unbiased_sample_gets_no_warning():
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], [], pd.DataFrame(),
                        {"selection_biased": False, "resolved": 400, "markets": 680,
                         "deadline_passed": 380, "base_rate": 0.31}, 1.0)
    assert "PnL is not yet a result" not in text


def test_a_run_with_nothing_resolved_still_produces_a_reliability_section():
    text = daily.report("2026-09-16", [(result_with(), {"model": "m", **sim.metrics(
        result_with())})], ["2026-09-16"], [], pd.DataFrame(),
        {"selection_biased": False}, 1.0)
    assert "no resolved position yet" in text


# --------------------------------------------------------- launchd wiring


def test_the_report_job_is_in_the_install_scripts_label_list():
    text = (SCHEDULING / "install.sh").read_text()
    assert "com.polyoptions.report" in text
    labels = re.search(r"LABELS=\(([^)]*)\)", text).group(1).split()
    assert labels == ["com.polyoptions.snapshot", "com.polyoptions.daily",
                      "com.polyoptions.report"]


def test_every_label_has_a_plist_beside_it():
    text = (SCHEDULING / "install.sh").read_text()
    labels = re.search(r"LABELS=\(([^)]*)\)", text).group(1).split()
    for label in labels:
        assert (SCHEDULING / f"{label}.plist").exists(), label


def test_every_placeholder_in_every_plist_is_substituted_by_install_sh():
    """The failure this prevents is silent: an unsubstituted `__REPORT_HOUR__`
    makes `plutil -lint` fail inside the install loop, and a placeholder that
    install.sh simply does not know about would install a job that never runs."""
    install = (SCHEDULING / "install.sh").read_text()
    for plist in SCHEDULING.glob("*.plist"):
        for placeholder in set(re.findall(r"__[A-Z_]+__", plist.read_text())):
            assert f"s|{placeholder}|" in install, f"{plist.name}: {placeholder}"


def test_the_report_plist_runs_daily_py_at_eighteen_hundred_eastern():
    text = (SCHEDULING / "com.polyoptions.report.plist").read_text()
    assert "model/daily.py" in text
    assert "__REPORT_HOUR__" in text and "__REPORT_MINUTE__" in text
    assert "18:00 America/New_York" in text


def test_install_sh_converts_both_job_times_not_just_the_daily_one():
    text = (SCHEDULING / "install.sh").read_text()
    assert "read -r DAILY_HOUR DAILY_MINUTE REPORT_HOUR REPORT_MINUTE" in text
    assert "((17, 30), (18, 0))" in text


# ----------------------------------------------------------- P6: rate bases


def test_the_daily_report_states_the_bankroll_its_percentages_are_of():
    """The `% bankroll` column is a rate; the report never printed the base it
    is a share of."""
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], [], pd.DataFrame(),
                        {"selection_biased": False}, 1.0)
    assert "$10,000" in text


def test_a_model_with_no_forecast_is_named_rather_than_silently_absent():
    """The random control carries no fair, so it has nothing to be scored on.
    Dropping its row leaves a reader to wonder whether it resolved nothing or
    was forgotten."""
    result, m = busy_run("random_control")
    for settlement in result.settlements:
        settlement.position.entry_fair = float("nan")
    text = daily.report("2026-09-16", [(result, m)], ["2026-09-16"], [],
                        pd.DataFrame(), {"selection_biased": False}, 1.0)
    assert "no forecast to score" in text
