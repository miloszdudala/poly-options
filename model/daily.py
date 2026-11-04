"""One command for the end of a session: price, re-run, report.

    .venv/bin/python model/daily.py [--date 2026-09-16] [--no-price]

Three steps, in this order, because each needs the one before it:

1. **Price** every session cycle that has books and is not yet in
   `model/store/fair`. Incremental by cycle, not by date, so a re-run after a
   half-collected session costs only the cycles it is missing.
2. **Re-run every model on every type** over the whole priced window through
   `run.run_matrix`, which is the same call `run_all.py` and `run.py` make. A
   daily report and an ad-hoc run must not be two different experiments.
3. **Write** `model/reports/daily_<date>.md`: per model x type the bets made
   today, the cumulative EV promised at entry against the PnL realised, the
   reliability table so far; then the collector's own gaps out of the `runs`
   table.

The collector section is not decoration. On 2026-09-17 the laptop slept and
four snapshot cycles were gathered over up to 126 minutes each; a simulation
that replays a day like that without saying so reports a thin market rather
than a sleeping machine.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see model/data.py

import data  # noqa: E402
import fair_schema  # noqa: E402
import results  # noqa: E402
from engine import price_cycle  # noqa: E402
from run import run_matrix  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
GAP_WINDOW_DAYS = 7
CADENCE_S = 900.0        # the snapshot job's own interval; see scheduling/
BARS_PROBE = "SPY"       # always collected; its bars stand for the day's
BARS_LAG = pd.Timedelta(minutes=10)   # bars are stamped at their start


# --------------------------------------------------------------- 1. price


def bars_cover(cycle_id: str) -> bool:
    """Have the 5-minute bars reached this cycle yet?

    Without them the touch check sees no evidence and calls every barrier
    untouched, and the spot falls back to Cboe's delayed last. 09-23, 09-29
    and 10-01 were priced that way during collector outages, never repriced
    once the bars arrived, and 40 fills landed on barriers that had already
    printed (review 2026-10-09). A deferred cycle stays unpriced, so the next
    run picks it up.
    """
    bars = data.bars_5m()
    spy = bars.loc[bars["symbol"] == BARS_PROBE, "bar_time"]
    if spy.empty:
        return False
    stamp = pd.Timestamp(dt.datetime.strptime(cycle_id[:16], "%Y%m%dT%H%M%SZ"), tz="UTC")
    return spy.max() >= stamp - BARS_LAG


def unpriced_cycles(dates: list[str], echo=None) -> dict[str, list[str]]:
    """Session cycles with books that the engine has not priced yet, minus
    those whose bars have not arrived (`bars_cover`)."""
    out = {}
    for date in dates:
        priced = set()
        frame = data.fair_values(date)
        if len(frame):
            priced = set(frame["cycle_id"].unique())
        missing = [c for c in data.session_cycles(date) if c not in priced]
        deferred = [c for c in missing if not bars_cover(c)]
        if deferred and echo:
            echo(f"  deferred {len(deferred)} cycle(s) on {date}: no 5-minute bars yet")
        missing = [c for c in missing if c not in deferred]
        if missing:
            out[date] = missing
    return out


def price_missing(dates: list[str], echo=None) -> list[dict]:
    """Price and store the missing cycles, one part file each.

    Appends rather than rewriting the partition, which is only safe because
    the cycles written are exactly the ones that were absent. `run_fair.py`
    clears the partition first for the same reason in reverse: it reprices
    everything.
    """
    stats = []
    for date, cycles in unpriced_cycles(dates, echo=echo).items():
        for cycle_id in cycles:
            frame, stat = price_cycle(cycle_id)
            if len(frame):
                fair_schema.write_fair(frame.to_dict("records"), date)
            stats.append(stat)
            if echo:
                echo(f"  priced {cycle_id}  {stat['markets']:4d} markets  "
                     f"{stat['priced']:4d} priced  {stat['seconds']:5.1f}s")
    return stats


# ------------------------------------------------------- 2. today's slice


def today_bets(result, date: str) -> dict:
    """Bets entered on one date, what went in, and the EV they promised."""
    bets = results.bets_frame(result)
    if bets.empty:
        return {"bets": 0, "money_in": 0.0, "promised_ev": 0.0}
    mine = bets[[data.cycle_date(c) == date for c in bets["entry_cycle_id"]]]
    return {"bets": int(len(mine)), "money_in": float(mine["cost_usd"].sum()),
            "promised_ev": float(mine["ev_usd"].sum())}


# ------------------------------------------------------ 3. collector gaps


def collector_gaps(since: str) -> pd.DataFrame:
    """One row per date: what the collector managed, and what it missed.

    Gaps are measured against the date's own median interval rather than
    against the nominal 15 minutes, because a snapshot that takes three
    minutes shifts every later tick and a fixed grid would call that a gap.
    A run longer than one cadence has overlapped the next tick, which is what
    a truncated cycle looks like from here.
    """
    runs = data.runs(since=since)
    if runs.empty:
        return pd.DataFrame()
    rows = []
    for date, group in runs.groupby("date"):
        snaps = group[(group["job"] == "snapshot") & group["in_session"]]
        starts = snaps["started_at_utc"].sort_values()
        gaps = starts.diff().dt.total_seconds().dropna()
        median = float(gaps.median()) if len(gaps) else float("nan")
        longest = float(gaps.max()) if len(gaps) else float("nan")
        skipped = int((gaps > 2 * median).sum()) if len(gaps) and np.isfinite(median) else 0
        rows.append({
            "date": date,
            "in_session_cycles": int(len(snaps)),
            "all_runs": int(len(group)),
            "median_gap_min": median / 60.0 if np.isfinite(median) else float("nan"),
            "longest_gap_min": longest / 60.0 if np.isfinite(longest) else float("nan"),
            "gaps_over_2x": skipped,
            "over_cadence": int((group["duration_seconds"] > CADENCE_S).sum()),
            "not_ok": int((group["status"] != "ok").sum()),
            "failures": int(group["failure_count"].fillna(0).sum()),
            "tokens_missing": int(np.nansum(group["missing"].values)),
        })
    return pd.DataFrame(rows).sort_values("date")


# ----------------------------------------------------------------- report


def _usd(x) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"${x:,.0f}"


def _usd2(x) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"${x:,.2f}"


def _n(x) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.1f}"


def report(date: str, runs: list, dates: list[str], priced: list[dict],
           gaps: pd.DataFrame, sample: dict, elapsed: float,
           note: str | None = None) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    through = runs[0][0].settled_through if runs else "-"
    lines = [
        f"# Daily - {date}",
        "",
        f"{stamp}. Window {dates[0]} to {dates[-1]} ({len(dates):,} date(s)); "
        f"{len(priced):,} cycle(s) priced this run; {elapsed:.0f}s. Outcomes read "
        f"through `{through}`. Account `max`." + (f" **{note}.**" if note else ""),
        "",
    ]
    if sample.get("selection_biased") or sample.get("settled_early", 0):
        lines += [
            f"> **PnL is not yet a result.** {sample.get('settled_early', 0):,} of the "
            f"{sample.get('resolved', 0):,} settled markets settled early by touching; "
            f"their untouched siblings have not reached a deadline. Only the "
            f"{sample.get('deadline_passed', 0):,} past their deadline are an unbiased "
            f"sample (base rate {sample.get('base_rate', float('nan')):.3f}).",
            "",
        ]

    lines += [f"## Today - {date}", "",
              "| model | type | bets | money in | promised EV |",
              "|---|---|---:|---:|---:|"]
    for result, m in runs:
        today = today_bets(result, date)
        lines.append(f"| {m['model']} | {m['type']} | {today['bets']:,} | "
                     f"{_usd(today['money_in'])} | {_usd2(today['promised_ev'])} |")
    lines += ["", "## Cumulative: promised against realised", "",
              results.summary_table([m for _, m in runs]), ""]

    lines += ["## Reliability so far", "",
              "| model | type | entry win prob | n | predicted | realised | cost | PnL |",
              "|---|---|---|---:|---:|---:|---:|---:|"]
    any_rows = False
    for result, m in runs:
        for row in results.reliability(results.bets_frame(result)).itertuples():
            any_rows = True
            lines.append(f"| {m['model']} | {m['type']} | {row.bucket} | {row.n:,} | "
                         f"{row.predicted:.3f} | {row.realised:.3f} | "
                         f"{_usd2(row.cost_usd)} | {_usd2(row.pnl_usd)} |")
    if not any_rows:
        lines.append("| - | - | no settled bet yet | 0 | - | - | - | - |")
    lines.append("")

    lines += [f"## Collector, last {GAP_WINDOW_DAYS} days", "",
              "| date | in-session cycles | runs | median gap | longest gap | gaps >2x | "
              "over cadence | not ok | failures | tokens missing |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    if gaps.empty:
        lines.append("| - | 0 | 0 | - | - | 0 | 0 | 0 | 0 | 0 |")
    else:
        for row in gaps.itertuples():
            lines.append(
                f"| {row.date} | {row.in_session_cycles:,} | {row.all_runs:,} | "
                f"{_n(row.median_gap_min)} | {_n(row.longest_gap_min)} | "
                f"{row.gaps_over_2x:,} | {row.over_cadence:,} | {row.not_ok:,} | "
                f"{row.failures:,} | {row.tokens_missing:,} |")
    lines += ["", f"Cadence is {CADENCE_S / 60:.0f} min; gaps are measured against each "
              "date's own median because a slow snapshot shifts every later tick. "
              "A run over one cadence has overlapped the next tick.", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------- main


def resolve_date(requested: str | None, dates: list[str]) -> tuple[str, str | None]:
    """The date the report is about, and a note if it is not today.

    Today in New York is the default because that is when the job fires. A
    weekend, a holiday or a day the collector missed has no priced cycles, and
    falling back to the newest priced date keeps the report readable while the
    collector table below it shows why today is absent.
    """
    if requested:
        return requested, None
    today = dt.datetime.now(EASTERN).date().isoformat()
    if today in dates:
        return today, None
    if not dates:
        return today, "nothing priced at all"
    return dates[-1], f"{today} has no priced session cycle; showing {dates[-1]}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="the date to report on; default today in New York")
    parser.add_argument("--no-price", action="store_true",
                        help="skip step 1 and report on what is already stored")
    args = parser.parse_args(argv)

    started = time.time()
    echo = lambda line: print(line, file=sys.stderr)          # noqa: E731
    with_books = _dates_with_books()
    priced_stats = [] if args.no_price else price_missing(with_books, echo=echo)

    dates = data.fair_dates()
    if not dates:
        echo("Nothing priced and nothing to price. Is the collector running?")
        return 1

    runs = run_matrix(dates, save=True, echo=echo)
    date, note = resolve_date(args.date, dates)
    if note:
        echo(f"  {note}")
    _, sample = results.universe_scores(dates)
    since = (dt.date.fromisoformat(dates[-1]) - dt.timedelta(days=GAP_WINDOW_DAYS)).isoformat()
    gaps = collector_gaps(since)

    text = report(date, runs, dates, priced_stats, gaps, sample,
                  time.time() - started, note)
    results.REPORTS.mkdir(parents=True, exist_ok=True)
    out = results.REPORTS / f"daily_{date}.md"
    out.write_text(text)
    echo(f"  wrote {out} ({len(text.splitlines())} lines)")
    print(text)
    return 0


def _dates_with_books() -> list[str]:
    """Every date the collector has books for. The session and excluded-date
    filters are applied per cycle by `data.session_cycles`, not here."""
    frame = data._q(f"select distinct date from {data._src('poly_books')} order by date")
    return frame["date"].astype(str).tolist()


if __name__ == "__main__":
    raise SystemExit(main())
