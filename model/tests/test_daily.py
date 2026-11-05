"""`daily.py`: the bar guard, today's slices, the collector's
gaps, and the launchd wiring that would otherwise fail silently at 18:00.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import daily
from reporting import results
import replay as sim
from pipeline import inputs
from pipeline.offers import YES

REPO = Path(__file__).resolve().parent.parent.parent
SCHEDULING = REPO / "scheduling"


# ------------------------------------------------------- 1. the bar guard


def _spy_bars_until(stamp):
    return pd.DataFrame({"symbol": ["SPY"], "bar_time": [pd.Timestamp(stamp, tz="UTC")]})


def test_the_bar_guard_passes_a_cycle_inside_the_newest_bar(monkeypatch):
    """Without the 5-minute bars the touch check sees nothing (review
    2026-10-09); `inputs.hit` refuses such a cycle and the cache keeps nothing."""
    monkeypatch.setattr(inputs.store, "bars_5m", lambda: _spy_bars_until("2026-10-01 19:55"))
    assert inputs.bars_cover("20261001T200001Z-a")
    monkeypatch.setattr(inputs.store, "bars_5m", lambda: _spy_bars_until("2026-10-01 19:45"))
    assert not inputs.bars_cover("20261001T200001Z-a")


def test_the_bar_guard_defers_everything_with_no_bars_at_all(monkeypatch):
    monkeypatch.setattr(inputs.store, "bars_5m",
                        lambda: pd.DataFrame({"symbol": [], "bar_time": []}))
    assert not inputs.bars_cover("20261001T140000Z-a")


# ------------------------------------------------------- 2. today's slice


def position(cycle_id="20260916T140000Z-a", win_prob=0.4, won=None, cost=100.0, ev=5.0):
    p = sim.Position(
        market_id=f"m{cycle_id}{win_prob}{won}", side=YES, ticker="NVDA",
        contract_type="hit_up", level=1.0, deadline=pd.Timestamp("2026-09-18T20:00:00Z"),
        period="weekly", direction="up", vol="wild", entry_cycle_id=cycle_id,
        entry_at=pd.Timestamp(sim.store.cycle_start(cycle_id)), win_prob=win_prob,
        shares=10.0, cost_usd=cost, ev_usd=ev)
    if won is not None:
        p.result = "Yes" if won else "No"
        p.payout_usd = 10.0 if won else 0.0
        p.settled_at = pd.Timestamp("2026-09-17T18:00:00Z")
    return p


def result_with(positions=(), cycles=("20260916T140000Z-a",)):
    return sim.Result(name="t", model="m", account="max", period="weekly",
                      cycles=list(cycles), positions=list(positions), seconds=1.0,
                      settled_through="2026-09-18T00:00:00Z", skipped={})


def test_todays_bets_count_only_today():
    positions = [position("20260916T140000Z-a", cost=10.0),
                 position("20260916T141500Z-b", cost=5.0),
                 position("20260917T140000Z-c", cost=99.0)]
    out = daily.today_bets(result_with(positions), "2026-09-16")
    assert out == {"bets": 2, "money_in": 15.0, "promised_ev": 10.0}


# ------------------------------------------------------ 3. collector gaps


def runs_frame(rows):
    frame = pd.DataFrame(rows)
    frame["started_at_utc"] = pd.to_datetime(frame["started_at_utc"], utc=True)
    return frame


def test_a_skipped_tick_shows_up_as_a_gap_over_twice_the_median(monkeypatch):
    """Measured against the date's own median, not against 15 minutes: a
    snapshot that takes three minutes shifts every later tick and a fixed grid
    would call that a gap."""
    monkeypatch.setattr(daily.store, "runs", lambda since: runs_frame([
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
    monkeypatch.setattr(daily.store, "runs", lambda since: runs_frame([
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
    monkeypatch.setattr(daily.store, "runs", lambda since: runs_frame([
        {"date": "2026-09-18", "job": "snapshot", "in_session": True,
         "started_at_utc": "2026-09-18T14:00:00Z", "duration_seconds": 40.0,
         "status": "ok", "failure_count": 0, "missing": 0.0}]))
    row = daily.collector_gaps("2026-09-11").iloc[0]
    assert np.isnan(row["median_gap_min"])
    assert row["gaps_over_2x"] == 0


def test_no_runs_at_all_gives_an_empty_frame_not_an_error(monkeypatch):
    monkeypatch.setattr(daily.store, "runs", lambda since: pd.DataFrame())
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


def busy_run(name):
    positions = [position(win_prob=p, won=w) for p, w in
                 ((0.07, False), (0.15, False), (0.3, True), (0.45, True),
                  (0.6, True), (0.7, False), (0.85, True), (0.97, True))]
    result = result_with(positions)
    result.model = name
    return result, results.summary(result)


def test_the_report_stays_under_a_hundred_and_twenty_lines():
    """The prompt's ceiling, and the reason the reliability tables are one
    table with a model column rather than five tables."""
    runs = [busy_run(n) for n in ("edge_taker", "random_control", "a", "b")]
    gaps = pd.DataFrame([{"date": f"2026-09-{d:02d}", "in_session_cycles": 21,
                          "all_runs": 28, "median_gap_min": 17.5,
                          "longest_gap_min": 41.2, "gaps_over_2x": 1,
                          "over_cadence": 2, "not_ok": 4, "failures": 10,
                          "tokens_missing": 1282} for d in range(11, 19)])
    text = daily.report("2026-09-16", runs, ["2026-09-16"], gaps,
                        {"selection_biased": True, "resolved": 51, "markets": 680,
                         "deadline_passed": 0, "base_rate": 1.0}, 13.0)
    assert len(text.splitlines()) < 120


def test_the_report_leads_with_the_selection_warning_when_the_sample_is_touches():
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], pd.DataFrame(),
                        {"selection_biased": True, "resolved": 51, "markets": 680,
                         "deadline_passed": 0, "base_rate": 1.0}, 1.0)
    warning = text.index("PnL is not yet a result")
    assert warning < text.index("## Today")


def test_the_fallback_date_note_reaches_the_report_not_just_the_terminal():
    """The report is the artifact; a reader opening `daily_2026-09-16.md` on
    the 18th has to be told it is not the 18th."""
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], pd.DataFrame(),
                        {"selection_biased": False}, 1.0,
                        note="2026-09-18 has no priced session cycle; showing 2026-09-16")
    assert "**2026-09-18 has no priced session cycle" in text.splitlines()[2]


def test_an_unbiased_sample_gets_no_warning():
    runs = [busy_run("edge_taker")]
    text = daily.report("2026-09-16", runs, ["2026-09-16"], pd.DataFrame(),
                        {"selection_biased": False, "resolved": 400, "markets": 680,
                         "deadline_passed": 380, "base_rate": 0.31}, 1.0)
    assert "PnL is not yet a result" not in text


def test_a_run_with_nothing_resolved_still_produces_a_reliability_section():
    text = daily.report("2026-09-16", [(result_with(), results.summary(result_with()))],
                        ["2026-09-16"], pd.DataFrame(), {"selection_biased": False}, 1.0)
    assert "no settled bet yet" in text


def test_the_report_sets_promised_ev_against_realised_pnl():
    text = daily.report("2026-09-16", [busy_run("edge_taker")], ["2026-09-16"],
                        pd.DataFrame(), {"selection_biased": False}, 1.0)
    assert "promised (settled)" in text and "PnL (settled)" in text


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
    assert "model/run.py" in text and "<string>daily</string>" in text
    assert "__REPORT_HOUR__" in text and "__REPORT_MINUTE__" in text
    assert "18:00 America/New_York" in text


def test_install_sh_converts_both_job_times_not_just_the_daily_one():
    text = (SCHEDULING / "install.sh").read_text()
    assert "read -r DAILY_HOUR DAILY_MINUTE REPORT_HOUR REPORT_MINUTE" in text
    assert "((17, 30), (18, 0))" in text
