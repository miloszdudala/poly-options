"""Run every model of MODELS section 5 over the priced window and report.

    .venv/bin/python model/run_all.py

Four models plus the random control, each through the same harness, each
writing `model/reports/<model>_<from>_<to>.md` and
`model/store/ledger/<model>/`. The combined report is
`model/reports/all_<from>_<to>.md`, and the summary table is printed.

Two things to read before the PnL:

* **the control**. It has no signal. If it earns what the models earn, the
  window drifted and nothing here has demonstrated an edge.
* **the Brier table**, which is the same for every model because it does not
  depend on one. MODELS 4.5 calls it the scientific result: whether the
  option-implied fair predicts touches better than the market's own mid is
  answerable on one day of data, and profitability is not.

The control's activity is set from `edge_taker`'s, so the two are controls for
each other's exposure rather than for two different experiments.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see model/data.py

import data  # noqa: E402
import sim  # noqa: E402
from models import (BalancedBook, DirectionBalanced, EdgeTaker, EmpiricalLongshot,  # noqa: E402
                    FactorNeutral, ScenarioHedged)

SUMMARY_COLUMNS = [
    ("model", "model", "{}"),
    ("positions", "positions_opened", "{:,}"),
    ("wanted $", "wanted_usd", "${:,.0f}"),
    ("filled $", "filled_usd", "${:,.0f}"),
    ("confirmed $", "cost_confirmed_usd", "${:,.0f}"),
    ("resolved", "positions_resolved", "{:,}"),
    ("PnL opt", "pnl_usd", "${:,.2f}"),
    ("PnL conf", "pnl_confirmed_usd", "${:,.2f}"),
    ("open @fair", "open_pnl_at_fair_usd", "${:,.2f}"),
    ("open @bid", "open_pnl_at_bid_usd", "${:,.2f}"),
    ("|beta$delta| max", "bdd_max_abs_frac", "{:.0%}"),
    ("top ticker", "largest_ticker_share", "{:.0%}"),
    ("worst day", "worst_day_usd", "${:,.2f}"),
]


def build_models(min_ev: float = 0.03, min_roi: float = 0.08, tag: str = "") -> list:
    """The four models of MODELS section 5, optionally at a different bar.

    `min_ev` / `min_roi` are MODELS 5.1's thresholds, shared by A, B and
    B-simple through `sim.candidate_edges`; C has its own shapes and is not
    re-thresholded here. `tag` is appended to every model's name so a sweep
    (`--min-ev 0.10 --tag ev10`) writes its own reports and ledgers instead of
    overwriting the default run's. The store is what makes this possible at
    any later date: books, fair values and resolutions are all on disk, and a
    replay at a new threshold is a 15-second run, not a new collection.
    """
    models = [EdgeTaker(min_ev=min_ev, min_roi=min_roi),
              FactorNeutral(min_ev=min_ev, min_roi=min_roi),
              BalancedBook(min_ev=min_ev, min_roi=min_roi),
              DirectionBalanced(min_ev=min_ev, min_roi=min_roi),
              ScenarioHedged(min_ev=min_ev, min_roi=min_roi),
              EmpiricalLongshot()]
    if tag:
        for model in models:
            model.name = f"{model.name}_{tag}"
    return models


def summary_table(rows: list[dict]) -> str:
    """The comparison table. Every number has its count in the row it sits in."""
    header = "| " + " | ".join(h for h, _, _ in SUMMARY_COLUMNS) + " |"
    rule = "|" + "|".join(["---"] + ["---:"] * (len(SUMMARY_COLUMNS) - 1)) + "|"
    lines = [header, rule]
    for row in rows:
        cells = []
        for _, key, fmt in SUMMARY_COLUMNS:
            value = row.get(key)
            if value is None or (isinstance(value, float) and not np.isfinite(value)):
                cells.append("-")
            else:
                cells.append(fmt.format(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def best_and_worst_days(daily: pd.DataFrame) -> dict:
    """MODELS 5.2: B's report shows its PnL on the best and worst SPY days.

    Marked PnL, not settled: the question is how the book behaved on the day
    the market moved, and a position bought on a -4% Tuesday that settles on
    Friday books all of its cash on Friday.
    """
    if daily.empty:
        return {}
    with_spy = daily[daily["spy_return"].notna()]
    # `pnl_days`, not `days`: `metrics` already owns `days` (session days
    # replayed) and this is the wider index that includes settlement-only days.
    out = {"worst_day_usd": float(daily["pnl_marked_usd"].min()),
           "worst_day_date": str(daily["pnl_marked_usd"].idxmin()),
           "pnl_days": int(len(daily))}
    if len(with_spy):
        worst = with_spy["spy_return"].idxmin()
        best = with_spy["spy_return"].idxmax()
        out |= {
            "worst_spy_date": str(worst), "worst_spy_return": float(with_spy.loc[worst, "spy_return"]),
            "pnl_on_worst_spy_day": float(with_spy.loc[worst, "pnl_marked_usd"]),
            "best_spy_date": str(best), "best_spy_return": float(with_spy.loc[best, "spy_return"]),
            "pnl_on_best_spy_day": float(with_spy.loc[best, "pnl_marked_usd"]),
            "spy_days": int(len(with_spy)),
        }
    return out


def run_one(model, dates: list[str], loader_factory) -> tuple[sim.Result, dict]:
    params = getattr(model, "params", None) or sim.Params()
    result = sim.run(model, params=params, loader=loader_factory())
    m = sim.metrics(result)
    m |= best_and_worst_days(sim.daily_pnl(result))
    m["refusals"] = dict(getattr(model, "refusals", {}) or {})
    sim.write_run(result, m)
    return result, m


def _headline_warning(rows: list[dict], sample: dict) -> str:
    """The one sentence that has to come before the PnL column is read."""
    if not sample.get("selection_biased"):
        early = sample.get("settled_early", 0)
        return ("Calibration is scored on the "
                f"{sample.get('deadline_passed', 0):,} markets past their deadline "
                f"(base rate {sample.get('base_rate', float('nan')):.3f})."
                + (f" Resolved PnL still includes {early:,} markets that settled "
                   "early by touching, whose untouched siblings are still open: "
                   "it leans toward YES winning and NO losing." if early else ""))
    resolved = sum(r.get("positions_resolved", 0) for r in rows)
    return (f"**Read the PnL columns as a coin that has not landed.** Every one "
            f"of the {sample['resolved']:,} settled markets in this window "
            f"resolved Yes and {sample['deadline_passed']:,} have reached their "
            f"deadline, because a hit market settles the instant it touches and "
            f"otherwise waits. So across the {resolved:,} resolved positions "
            f"here, every NO lost and every YES won by construction, and the "
            f"differences between models below are differences in which side "
            f"they took, not in whether they were right.")


def run_everything(dates: list[str], control: bool = True, echo=None,
                   min_ev: float = 0.03, min_roi: float = 0.08, tag: str = ""):
    """Every model plus the control over `dates`, each writing its own report.

    Returns `[(Result, metrics), ...]` in the order MODELS section 5 lists
    them, control last. `daily.py` and this module's `main` both go through
    here so that a daily report and an ad-hoc run cannot silently be two
    different experiments.
    """
    out = []
    for model in build_models(min_ev=min_ev, min_roi=min_roi, tag=tag):
        began = time.time()
        result, m = run_one(model, dates, lambda: sim.Loader(dates))
        out.append((result, m))
        if echo:
            echo(f"  {m['model']:<20} {m['positions_opened']:4,} positions  "
                 f"${m['filled_usd']:9,.0f} filled  {time.time() - began:5.1f}s")
    if control:
        # Same activity level as A, so the control controls for A's exposure
        # rather than for a quieter strategy nobody ran.
        per_cycle = max(1, round(out[0][1]["orders"] / max(out[0][1]["cycles"], 1)))
        control_model = sim.RandomControl(orders_per_cycle=per_cycle)
        if tag:
            control_model.name = f"{control_model.name}_{tag}"
        result, m = run_one(control_model, dates, lambda: sim.Loader(dates))
        out.append((result, m))
        if echo:
            echo(f"  {'random_control':<20} {m['positions_opened']:4,} positions  "
                 f"${m['filled_usd']:9,.0f} filled  ({per_cycle} orders/cycle)")
    return out


def combined_report(rows: list[dict], scores: pd.DataFrame, sample: dict,
                    dates: list[str], elapsed: float) -> str:
    start, end = dates[0], dates[-1]
    lines = [
        f"# All models — {start} to {end}",
        "",
        f"{len(dates)} priced date(s), {elapsed:.0f}s. One row per model; each "
        f"model's own report has the per-cycle detail.",
        "",
        "## Summary",
        "",
        summary_table(rows),
        "",
        "`PnL opt` is every fill; `PnL conf` counts only fills whose price level "
        "was still showing at the next cycle (MODELS 4.2). Headline PnL is "
        "resolved-only (MODELS 4.3); the open book is shown beside it at the "
        "model's fair and at the last bid. `worst day` is marked PnL.",
        "",
        _headline_warning(rows, sample),
        "",
        f"Bankroll ${rows[0]['params_bankroll']:,.0f} for every model, which is "
        f"what `|beta$delta| max` is a share of. `top ticker` is a share of the "
        f"dollars a model actually deployed (its `filled $`), not of bankroll, "
        f"so a model that deployed $600 can show 66% there while sitting far "
        f"inside an 8%-of-bankroll cap. `|beta$delta| max` is a mark-to-market "
        f"sensitivity, not an exposure to loss: every position here is bought, "
        f"so the most it can lose is what it paid.",
        "",
    ]

    lines += ["## A against B on the market's worst and best days (MODELS 5.2)", ""]
    named = {r["model"]: r for r in rows}
    if any("worst_spy_date" in r for r in rows):
        lines += ["| model | worst SPY day | SPY | PnL | best SPY day | SPY | PnL | days |",
                  "|---|---|---:|---:|---|---:|---:|---:|"]
        for row in rows:
            if "worst_spy_date" not in row:
                continue
            lines.append(
                f"| {row['model']} | {row['worst_spy_date']} | "
                f"{row['worst_spy_return']:+.2%} | ${row['pnl_on_worst_spy_day']:,.2f} | "
                f"{row['best_spy_date']} | {row['best_spy_return']:+.2%} | "
                f"${row['pnl_on_best_spy_day']:,.2f} | {row['spy_days']} |")
        lines.append("")
        if len({r.get("worst_spy_date") for r in rows if "worst_spy_date" in r}) == 1:
            lines += ["The window has one session with a SPY return, so the worst and "
                      "the best day are the same day and this table compares four "
                      "models on one observation. It is here because MODELS 5.2 asks "
                      "for it, not because it decides anything.", ""]

    lines += ["## Constraints that bound", ""]
    for row in rows:
        blockers = ", ".join(f"`{k}` {v:,}" for k, v in
                             sorted(row.get("blockers", {}).items(), key=lambda kv: -kv[1]))
        refusals = ", ".join(f"`{k}` {v:,}" for k, v in
                             sorted(row.get("refusals", {}).items(), key=lambda kv: -kv[1]))
        lines.append(f"- **{row['model']}** — harness: {blockers or 'none'}"
                     + (f"; model (last cycle): {refusals}" if refusals else ""))
    lines.append("")

    lines += ["## Brier scores over the whole priced universe (MODELS 4.5)", "",
              "Model-independent: one row per market at its first priced cycle of "
              "the window, scored against its resolution. Lower is better; "
              "`brier_vs_base_rate` below zero means the estimate beats always "
              "predicting the base rate.", ""]
    if sample.get("selection_biased"):
        lines += [
            f"**This table does not mean what it looks like.** Of "
            f"{sample['markets']:,} priced markets, {sample['resolved']:,} have "
            f"settled and **{sample['deadline_passed']:,} have reached their "
            f"deadline**; the base rate is {sample['base_rate']:.3f}. A hit "
            f"market settles the instant it touches and otherwise waits for its "
            f"deadline, so a window shorter than the markets' horizons resolves "
            f"nothing but touches. Every outcome in this sample is a 1, and the "
            f"ranking below is therefore a ranking of which estimate is *highest*, "
            f"not which is best calibrated - which is why `fair_cont`, the "
            f"deliberate upper bound of MODELS 3.3, comes top. There is no "
            f"unbiased calibration sample in this window at all. The scores "
            f"become meaningful once the 2026-09-18 and 2026-09-30 deadlines "
            f"pass; until then this section is a placeholder that runs, not a "
            f"result to act on.", ""]
    if len(scores):
        lines += ["| estimate | n | Brier | vs base rate | mean estimate | base rate |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in scores.itertuples():
            lines.append(f"| `{row.estimate}` | {row.n:,} | {row.brier:.4f} | "
                         f"{row.brier_vs_base_rate:+.4f} | {row.mean_estimate:.3f} | "
                         f"{row.base_rate:.3f} |")
    else:
        lines.append("No priced market in the window has resolved yet.")
    lines.append("")

    control = named.get("random_control")
    if control:
        lines += ["## The control", "",
                  f"`random_control` opened {control['positions_opened']:,} positions "
                  f"with no signal at all and resolved {control['positions_resolved']:,} "
                  f"of them for ${control['pnl_usd']:,.2f} optimistic / "
                  f"${control['pnl_confirmed_usd']:,.2f} confirmed. MODELS 4.5: if it "
                  f"earns what the models earn, the window drifted.", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", help="comma-separated; default is every priced date")
    parser.add_argument("--no-control", action="store_true")
    parser.add_argument("--min-ev", type=float, default=0.03,
                        help="MODELS 5.1 edge bar for A, B, B-simple (default 0.03)")
    parser.add_argument("--min-roi", type=float, default=0.08,
                        help="MODELS 5.1 ROI bar for A, B, B-simple (default 0.08)")
    parser.add_argument("--tag", default="",
                        help="suffix for report and ledger names, e.g. ev10")
    args = parser.parse_args(argv)

    dates = args.dates.split(",") if args.dates else data.fair_dates()
    if not dates:
        print("Nothing priced. Run model/run_fair.py --date <date> first.", file=sys.stderr)
        return 1

    started = time.time()
    runs = run_everything(dates, control=not args.no_control,
                          echo=lambda line: print(line, file=sys.stderr),
                          min_ev=args.min_ev, min_roi=args.min_roi, tag=args.tag)
    rows = [m for _, m in runs]

    scores, sample = sim.universe_scores(dates)
    text = combined_report(rows, scores, sample, dates, time.time() - started)
    suffix = f"_{args.tag}" if args.tag else ""
    out = sim.REPORTS / f"all_{dates[0]}_{dates[-1]}{suffix}.md"
    sim.REPORTS.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"  wrote {out}", file=sys.stderr)
    print(summary_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
