"""The whole pipeline, end to end, on the synthetic store (`synthetic.py`).

The pinned numbers below are what the code before the 2026-11-05 restructure
(commit b33d661: regular-hours pricing, venue fees) produced on the same store
- edge_taker / random_control on the weekly and monthly hit types and the
weekly close-above type, bet for bet - so a refactor that moves them has
changed behaviour.
"""
from __future__ import annotations

import pandas as pd
import pytest

import lab
import replay
import sample
import store
from pipeline import cache, config as configs
from pipeline.core import Cycle, get
from reporting import results

DATES = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]


def totals(name, overrides=None):
    result = replay.run(configs.load(name, overrides), DATES)
    m = results.summary(result)
    return m["bets"], round(m["money_in"], 2), round(m["pnl"], 2), m["settled"]


@pytest.mark.parametrize("name,expected", [
    ("mvp", (8, 2.76, -0.76, 8)),              # one share, no YES premium (2026-11-18)
    ("control", (8, 572.50, -150.99, 8)),
    ("hit_monthly", (3, 1.05, -0.22, 1)),
    ("close_above_weekly", (6, 2.14, 0.86, 6)),
])
def test_the_replays_match_the_code_before_the_restructure(synthetic_store, name, expected):
    assert totals(name) == expected


def test_every_market_of_a_cycle_has_a_fair_or_the_gate_that_stopped_it(synthetic_store):
    cycle_id = store.session_cycles("2026-09-16")[0]
    frame = lab.inspect(configs.load("mvp"), cycle_id)
    assert len(frame) == 8                                     # 2 tickers x 4 weekly barriers
    assert (frame["fair"].notna() | frame["gate"].notna()).all()
    assert set(frame["gate"].dropna()) <= {"touch_pending", "deep_otm", "earnings_gap",
                                           "quote_ahead_of_book"} | {
        r for r in frame["gate"].dropna() if r.startswith("no ")}


def test_the_cache_is_read_back_and_keyed_by_the_settings(synthetic_store):
    config = configs.load("mvp")
    cycle_id = store.session_cycles("2026-09-15")[0]
    first = cache.priced(Cycle(cycle_id, ("hit_up", "hit_down")), config)
    assert list(cache.ROOT.rglob(f"{cycle_id}.parquet"))
    again = cache.priced(Cycle(cycle_id, ("hit_up", "hit_down")), config)
    assert list(again["market_id"]) == list(first["market_id"])
    pd.testing.assert_series_equal(first["fair"], again["fair"], check_dtype=False)
    assert list(again["gate"].isna()) == list(first["gate"].isna())
    atm = configs.load("mvp", ["vol.use=atm_first_expiry_after"])
    assert cache.key(atm) != cache.key(config)
    assert cache.key(configs.load("mvp", ["bar.min_ev=0.10"])) == cache.key(config)


def test_a_config_extends_the_mvp_and_changes_only_what_it_lists():
    mvp, monthly, chain = (configs.load(n) for n in ("mvp", "hit_monthly", "close_above_weekly"))
    assert monthly["markets"] == {**mvp["markets"], "period": "monthly"}
    assert {k: v for k, v in monthly.items() if k not in ("markets", "name")} == \
        {k: v for k, v in mvp.items() if k not in ("markets", "name")}
    assert chain["vol"] == {"use": "none"} and chain["bar"] == mvp["bar"]


def test_an_override_changes_one_setting_and_names_the_run():
    config = configs.load("mvp", ["bar.min_ev=0.1"])
    assert config["bar"]["min_ev"] == 0.1 and config["name"] == "mvp+bar.min_ev=0.1"


def test_an_unknown_variant_names_the_known_ones():
    with pytest.raises(KeyError, match="atm_first_expiry_after"):
        get("vol", "nope")


def test_lab_scores_two_vol_variants_against_outcomes(synthetic_store):
    table = lab.score("mvp", "vol.use=atm_first_expiry_after,strike_first_expiry_after", [], DATES)
    fair = table[table["estimate"] == "fair"]
    assert set(fair["variant"]) == {"vol.use=atm_first_expiry_after",
                                    "vol.use=strike_first_expiry_after"}
    assert (fair["n"] > 0).all() and fair["brier"].between(0, 1).all()


def test_lab_sweeps_a_decision_setting(synthetic_store):
    table = lab.sweep("mvp", "bar.min_ev=0.03,0.5", [], DATES)
    loose, strict = table["bets"]
    assert loose == 8 and strict < loose


def test_a_sample_cut_from_a_store_replays_on_its_own(synthetic_store, tmp_path):
    out = sample.cut("2026-09-14", "2026-09-18", ["NVDA"], tmp_path / "nvda")
    store.use(out)
    result = replay.run(configs.load("mvp"), DATES)
    assert result.positions and {p.ticker for p in result.positions} == {"NVDA"}


def test_the_opening_gate_rules_out_books_read_before_ten():
    from pipeline import gates
    m = pd.DataFrame({"fetched_at_utc": ["2026-11-16T14:45:00Z", "2026-11-16T15:00:00Z",
                                         "2026-11-16T15:15:00Z"]})        # 9:45, 10:00, 10:15 ET
    assert list(gates.opening(m, None, {"opening_minutes": 30})) == [True, False, False]


def test_deadline_day_gate_covers_the_deadline_s_own_session_only():
    from pipeline import gates
    m = pd.DataFrame({"T_sessions": [0.2, 1.0, 1.01, 4.5]})
    assert list(gates.deadline_day(m, None, {})) == [True, True, False, False]


def test_the_earnings_gate_holds_until_the_report_and_lets_go_after(monkeypatch):
    from pipeline import gates
    when = pd.Timestamp("2026-11-17T21:00:00Z")                         # Tuesday, after the close
    monkeypatch.setattr(store, "upcoming_earnings", lambda: pd.DataFrame({
        "symbol": ["NVDA"], "when": [when], "first_seen": [pd.Timestamp("2026-09-16", tz="UTC")]}))
    deadline = pd.Timestamp("2026-11-20T21:00:00Z")
    m = pd.DataFrame({"ticker": ["NVDA", "NVDA", "AMD"], "deadline": deadline,
                      "fetched_at_utc": ["2026-11-17T18:00:00Z", "2026-11-18T15:00:00Z",
                                         "2026-11-17T18:00:00Z"]})
    assert list(gates.earnings_inside(m, None, {})) == [True, False, False]
