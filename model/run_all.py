"""Run every model on every type under the `max` account, save, and report.

    .venv/bin/python model/run_all.py [--dates 2026-09-16,...] [--tag name]

Each model x type writes `model/reports/<model>__<type>[_tag]_<from>_<to>.md`
and `model/store/ledger/<model>__<type>[_tag]/`. The combined report is
`model/reports/all_<from>_<to>[_tag].md`, and the summary table is printed.

Two things to read before the PnL:

* **the control** (`random_control`). It has no signal. If it earns what the
  models earn, the window drifted and nothing here has demonstrated an edge.
* **the Brier table**, the same for every model because it does not depend on
  one: whether the option-implied fair predicts touches better than the
  market's own mid.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see model/data.py

import data  # noqa: E402
import results  # noqa: E402
from run import run_matrix  # noqa: E402


def headline_warning(rows: list[dict], sample: dict) -> str:
    """The one sentence that has to come before the PnL column is read."""
    if not sample.get("selection_biased"):
        early = sample.get("settled_early", 0)
        return ("Calibration is scored on the "
                f"{sample.get('deadline_passed', 0):,} markets past their deadline "
                f"(base rate {sample.get('base_rate', float('nan')):.3f})."
                + (f" Resolved PnL still includes {early:,} markets that settled "
                   "early by touching, whose untouched siblings are still open: "
                   "it leans toward YES winning and NO losing." if early else ""))
    resolved = sum(r.get("settled", 0) for r in rows)
    return (f"**Read the PnL columns as a coin that has not landed.** Every one "
            f"of the {sample['resolved']:,} settled markets in this window "
            f"resolved Yes and {sample['deadline_passed']:,} have reached their "
            f"deadline, because a hit market settles the instant it touches and "
            f"otherwise waits. So across the {resolved:,} settled bets "
            f"here, every NO lost and every YES won by construction, and the "
            f"differences between models below are differences in which side "
            f"they took, not in whether they were right.")


def brier_section(scores: pd.DataFrame, sample: dict) -> list[str]:
    lines = ["## Brier scores over the whole priced universe (MODELS 4.5)", "",
             "Hit markets only, model-independent: one row per market at its first "
             "priced cycle of the window, scored against its resolution. Lower is "
             "better; `brier_vs_base_rate` below zero means the estimate beats "
             "always predicting the base rate.", ""]
    if sample.get("selection_biased"):
        lines += [
            f"**This table does not mean what it looks like.** Of "
            f"{sample['markets']:,} priced markets, {sample['resolved']:,} have "
            f"settled and **{sample['deadline_passed']:,} have reached their "
            f"deadline**; the base rate is {sample['base_rate']:.3f}. A hit market "
            f"settles the instant it touches and otherwise waits for its deadline, "
            f"so a window shorter than the markets' horizons resolves nothing but "
            f"touches, and the ranking below is a ranking of which estimate is "
            f"*highest*, not which is best calibrated.", ""]
    if len(scores):
        lines += ["| estimate | n | Brier | vs base rate | mean estimate | base rate |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in scores.itertuples():
            lines.append(f"| `{row.estimate}` | {row.n:,} | {row.brier:.4f} | "
                         f"{row.brier_vs_base_rate:+.4f} | {row.mean_estimate:.3f} | "
                         f"{row.base_rate:.3f} |")
    else:
        lines.append("No priced market in the window has resolved yet.")
    return lines + [""]


def combined_report(rows: list[dict], scores: pd.DataFrame, sample: dict,
                    dates: list[str], elapsed: float) -> str:
    lines = [
        f"# All models - {dates[0]} to {dates[-1]}",
        "",
        f"{len(dates)} date(s), {elapsed:.0f}s, account `max`. One row per model x "
        f"type; each has its own report with the per-period detail.",
        "",
        "## Promised against realised",
        "",
        results.summary_table(rows),
        "",
        "`promised EV` is the EV at entry of every bet; `promised (settled)` and "
        "`PnL (settled)` are the same settled bets, so their ratio is "
        "`realised / promised`. Open bets are excluded from PnL (MODELS 4.3).",
        "",
        headline_warning(rows, sample),
        "",
    ]
    notes = {r["type"]: r["note"] for r in rows if r.get("note")}
    if notes:
        lines += ["## Notes on types", ""]
        lines += [f"- **{t}**: {n}" for t, n in sorted(notes.items())]
        lines.append("")
    skipped = [r for r in rows if r.get("skipped")]
    if skipped:
        lines += ["## Markets skipped", ""]
        for r in skipped:
            parts = ", ".join(f"{k} {v:,}" for k, v in sorted(r["skipped"].items()))
            lines.append(f"- **{r['model']} / {r['type']}**: {parts}")
        lines.append("")
    lines += brier_section(scores, sample)
    for r in rows:
        if r["model"] == "random_control":
            lines.append(f"`random_control` on `{r['type']}`: {r['bets']:,} bets with no "
                         f"signal, PnL ${r['pnl']:,.2f} on {r['settled']:,} settled. "
                         "MODELS 4.5: if it earns what the models earn, the window drifted.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates", help="comma-separated; default every priced date")
    parser.add_argument("--tag", default="", help="suffix for report and ledger names")
    args = parser.parse_args(argv)

    dates = sorted(args.dates.split(",")) if args.dates else data.fair_dates()
    if not dates:
        print("Nothing priced. Run model/run_fair.py --date <date> first.", file=sys.stderr)
        return 1
    started = time.time()
    runs = run_matrix(dates, save=True, tag=args.tag,
                      echo=lambda line: print(line, file=sys.stderr))
    rows = [m for _, m in runs]
    scores, sample = results.universe_scores(dates)
    text = combined_report(rows, scores, sample, dates, time.time() - started)
    suffix = f"_{args.tag}" if args.tag else ""
    out = results.REPORTS / f"all_{dates[0]}_{dates[-1]}{suffix}.md"
    results.REPORTS.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"  wrote {out}", file=sys.stderr)
    print(results.summary_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
