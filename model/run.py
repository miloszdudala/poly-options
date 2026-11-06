"""The one command for `model/`.

    run.py [--store PATH] [replay] [--config mvp] [--set step.key=value] [--from D] [--to D] [--save]
    run.py daily                 # replay the routine configs on every date, write the daily report
    run.py lab inspect|score|sweep ...
    run.py sample --from D --to D --tickers T,... --out DIR  |  --synthetic --out DIR
    run.py dashboard
    run.py review

`replay` (the default) runs each `--config` (default: the routine ones,
`mvp` and `control`) over the dates and prints bets, money in, promised EV
and PnL by period; `--save` writes ledgers, reports and the combined report.
`--store` reads a sample or the synthetic store instead of the full one.
Configs live in `model/configs/`; `--set` changes one setting for one run.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see store.py

import store  # noqa: E402
import pipeline  # noqa: E402,F401  (registers the step variants)
import replay  # noqa: E402
from pipeline import config as configs  # noqa: E402
from reporting import results  # noqa: E402

ROUTINE = ["mvp", "control"]


def date_range(start: str | None, end: str | None) -> list[str]:
    """Every store date from `start` to `end` (default: all of them)."""
    dates = store.dates()
    return [d for d in dates if (not start or d >= start) and (not end or d <= end)]


def run_configs(names: list[str], dates: list[str], overrides: list[str] | None = None,
                save: bool = False, tag: str = "", use_cache: bool = True,
                echo=None) -> list[tuple[replay.Result, dict]]:
    out = []
    for name in names:
        result = replay.run(configs.load(name, overrides), dates, use_cache=use_cache)
        m = results.summary(result)
        if save:
            results.write_run(result, tag, m)
        out.append((result, m))
        if echo:
            echo(f"  {result.name:<28} {m['bets']:5,} bets  ${m['money_in']:10,.0f} in  "
                 f"{m['seconds']:6.1f}s")
    return out


def short_report(result: replay.Result, m: dict) -> str:
    lines = [f"{m['type']} ({m['model']}, {m['account']}): {m['bets']:,} bets, "
             f"${m['money_in']:,.2f} in, promised EV ${m['promised_ev']:,.2f}; "
             f"{m['settled']:,} settled: PnL ${m['pnl']:,.2f} against "
             f"${m['settled_promised_ev']:,.2f} promised"]
    for period, row in results.by_period(result).iterrows():
        lines.append(f"  {result.period} {period}: {int(row['bets']):4,} bets  "
                     f"${row['money_in']:9,.2f} in  promised ${row['promised_ev']:8,.2f}  "
                     f"PnL ${row['pnl']:9,.2f} on {int(row['settled']):,} settled")
    if m["skipped"]:
        lines.append("  ruled out: " + ", ".join(f"{k} {v:,}" for k, v in m["skipped"].items()))
    return "\n".join(lines)


def replay_main(argv) -> int:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", action="append", help="repeatable; default mvp and control")
    parser.add_argument("--set", dest="overrides", action="append", default=[])
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--dates", help="comma-separated, instead of --from/--to")
    parser.add_argument("--save", action="store_true", help="write ledgers and reports")
    parser.add_argument("--tag", default="", help="suffix for saved ledger and report names")
    parser.add_argument("--no-cache", action="store_true", help="reprice instead of reading the cache")
    args = parser.parse_args(argv)
    dates = sorted(args.dates.split(",")) if args.dates else date_range(args.start, args.end)
    if not dates:
        print("No dates with books in the store.", file=sys.stderr)
        return 1
    started = time.time()
    runs = run_configs(args.config or ROUTINE, dates, args.overrides, args.save, args.tag,
                       not args.no_cache, echo=lambda line: print(line, file=sys.stderr))
    for result, m in runs:
        print(short_report(result, m))
    if args.save:
        import lab

        rows = [m for _, m in runs]
        scores, sample = lab.brier(runs[0][0].config, dates)
        text = results.combined_report(rows, scores, sample, dates, time.time() - started)
        suffix = f"_{args.tag}" if args.tag else ""
        out = results.REPORTS / f"all_{dates[0]}_{dates[-1]}{suffix}.md"
        results.REPORTS.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"  wrote {out}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "--store":
        store.use(argv[1])
        argv = argv[2:]
    command = argv[0] if argv and not argv[0].startswith("-") else "replay"
    rest = argv[1:] if argv and argv[0] == command else argv
    if command == "replay":
        return replay_main(rest)
    if command == "lab":
        import lab
        return lab.main(rest)
    if command == "sample":
        import sample
        return sample.main(rest)
    if command == "daily":
        import daily
        return daily.main(rest)
    if command == "dashboard":
        from reporting import dashboard
        return dashboard.main(rest)
    if command == "review":
        from reporting import review
        return review.main(rest)
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
