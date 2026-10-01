"""One command for the end of a session: price, re-run, report.

    .venv/bin/python model/daily.py [--date 2026-09-16] [--no-price]

Three steps, in this order, because each needs the one before it:

1. **Price** every session cycle that has books and is not yet in
   `model/store/fair`. Incremental by cycle, not by date, so a re-run after a
   half-collected session costs only the cycles it is missing.
2. **Re-run every model** over the whole priced window through
   `run_all.run_everything`, which is the same call `run_all.py` makes. A daily
   report and an ad-hoc run must not be two different experiments.
3. **Write** `model/reports/daily_<date>.md`: per model the cumulative resolved
   PnL on confirmed fills, the open book at fair and at bid, the reliability
   table so far, the worst day, and today's exposure and capacity; then the
   collector's own gaps out of the `runs` table.

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
import run_all  # noqa: E402
import sim  # noqa: E402
from engine import price_cycle  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
GAP_WINDOW_DAYS = 7
CADENCE_S = 900.0        # the snapshot job's own interval; see scheduling/


# --------------------------------------------------------------- 1. price


def unpriced_cycles(dates: list[str]) -> dict[str, list[str]]:
    """Session cycles with books that the engine has not priced yet."""
    out = {}
    for date in dates:
        priced = set()
        frame = data.fair_values(date)
        if len(frame):
            priced = set(frame["cycle_id"].unique())
        missing = [c for c in data.session_cycles(date) if c not in priced]
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
    for date, cycles in unpriced_cycles(dates).items():
        for cycle_id in cycles:
            frame, stat = price_cycle(cycle_id)
            if len(frame):
                fair_schema.write_fair(frame.to_dict("records"), date)
            stats.append(stat)
            if echo:
                echo(f"  priced {cycle_id}  {stat['markets']:4d} markets  "
                     f"{stat['priced']:4d} priced  {stat['seconds']:5.1f}s")
    return stats


# ------------------------------------------------------- 2. today's slices


def today_capacity(result: sim.Result, date: str) -> dict:
    """Orders, dollars wanted and dollars filled on one date only."""
    frame = result.capacity
    if frame.empty:
        return {"orders": 0, "wanted_usd": 0.0, "filled_usd": 0.0}
    mine = frame[[data.cycle_date(c) == date for c in frame["cycle_id"]]]
    return {"orders": int(len(mine)),
            "wanted_usd": float(mine["wanted_usd"].sum()),
            "filled_usd": float(mine["filled_usd"].sum())}


def today_exposure(result: sim.Result, date: str) -> dict:
    """Net beta-dollar-delta at the last cycle of the date, and the day's peak.

    The closing number is the one MODELS 5.2's band is a statement about; the
    peak is the one that says whether the band held during the session, and on
    2026-09-16 those two differ by a factor of five for `factor_neutral`.
    """
    equity = result.equity
    if equity.empty:
        return {"bdd_close": float("nan"), "bdd_peak": float("nan"), "cycles": 0}
    mine = equity[equity["date"] == date]
    if mine.empty:
        return {"bdd_close": float("nan"), "bdd_peak": float("nan"), "cycles": 0}
    bdd = mine["beta_dollar_delta"].astype(float)
    peak = bdd.iloc[int(np.nanargmax(np.abs(bdd.values)))] if len(bdd) else float("nan")
    return {"bdd_close": float(bdd.iloc[-1]), "bdd_peak": float(peak),
            "cycles": int(len(mine))}


def today_fills(result: sim.Result, date: str) -> dict:
    fills = sim.fills_frame(result)
    if fills.empty:
        return {"fills": 0, "confirmed_usd": 0.0}
    mine = fills[fills["date"] == date]
    return {"fills": int(len(mine)),
            "confirmed_usd": float(mine[mine["confirmed"]]["cost_usd"].sum())}


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
    cycles = len(runs[0][0].cycles) if runs else 0
    lines = [
        f"# Daily — {date}",
        "",
        f"{stamp}. Window {dates[0]} to {dates[-1]}, {cycles:,} priced cycles over "
        f"{len(dates):,} date(s); {len(priced):,} cycle(s) priced this run; "
        f"{elapsed:.0f}s. Outcomes read through `{through}`."
        + (f" **{note}.**" if note else ""),
        "",
    ]
    if not sample.get("selection_biased") and sample.get("settled_early", 0):
        lines += [
            f"> **PnL is not yet a result.** {sample['settled_early']:,} of the "
            f"{sample['resolved']:,} settled markets settled early by touching; "
            f"their untouched siblings have not reached a deadline, so settled "
            f"PnL counts touched outcomes and not the others. Only the "
            f"{sample['deadline_passed']:,} past their deadline are an unbiased "
            f"sample (base rate {sample['base_rate']:.3f}).",
            "",
        ]
    elif sample.get("selection_biased"):
        lines += [
            f"> **PnL is not yet a result.** {sample['resolved']:,} of "
            f"{sample['markets']:,} priced markets have settled and "
            f"{sample['deadline_passed']:,} have reached a deadline, so the "
            f"sample is every market that touched and none that did not "
            f"(base rate {sample['base_rate']:.3f}). Every NO lost and every YES "
            f"won by construction. Read capacity and exposure.",
            "",
        ]

    bankroll = runs[0][0].params.bankroll if runs else 0.0
    lines += [f"## Today — {date}", "",
              f"Bankroll {_usd(bankroll)}; the `% bankroll` column is a share of it.",
              "",
              "| model | cycles | orders | wanted $ | filled $ | fills | confirmed $ | "
              "net beta$delta | peak | % bankroll |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for result, m in runs:
        cap = today_capacity(result, date)
        exp = today_exposure(result, date)
        fil = today_fills(result, date)
        pct = (abs(exp["bdd_peak"]) / result.params.bankroll
               if np.isfinite(exp["bdd_peak"]) else float("nan"))
        lines.append(
            f"| {m['model']} | {exp['cycles']:,} | {cap['orders']:,} | "
            f"{_usd(cap['wanted_usd'])} | {_usd(cap['filled_usd'])} | {fil['fills']:,} | "
            f"{_usd(fil['confirmed_usd'])} | {_usd(exp['bdd_close'])} | "
            f"{_usd(exp['bdd_peak'])} | "
            f"{'-' if not np.isfinite(pct) else f'{pct:.0%}'} |")
    lines.append("")

    lines += ["## Cumulative", "",
              "| model | positions | resolved | PnL confirmed | PnL optimistic | "
              "open cost | open @fair | open @bid | worst day | capital-days |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for result, m in runs:
        lines.append(
            f"| {m['model']} | {m['positions_opened']:,} | {m['positions_resolved']:,} | "
            f"{_usd2(m['pnl_confirmed_usd'])} | {_usd2(m['pnl_usd'])} | "
            f"{_usd(m['open_cost_usd'])} | {_usd2(m['open_pnl_at_fair_usd'])} | "
            f"{_usd2(m['open_pnl_at_bid_usd'])} | "
            f"{_usd2(m.get('worst_day_usd'))} | {_n(m['capital_days'])} |")
    lines += ["", "`PnL confirmed` counts only fills whose price level still showed at "
              "the next cycle (MODELS 4.2); open positions are marked both ways and "
              "excluded from the headline (MODELS 4.3).", ""]

    lines += ["## Reliability so far", "",
              "| model | entry win prob | n | predicted | realised | cost | PnL |",
              "|---|---|---:|---:|---:|---:|---:|"]
    any_rows = False
    for result, m in runs:
        settled = sim.settlements_frame(result)
        table = sim.reliability(settled)
        if len(table):
            any_rows = True
            for row in table.itertuples():
                lines.append(f"| {m['model']} | {row.bucket} | {row.n:,} | "
                             f"{row.predicted:.3f} | {row.realised:.3f} | "
                             f"{_usd2(row.cost_usd)} | {_usd2(row.pnl_usd)} |")
        elif len(settled):
            # Resolved positions but no fair to score them against: the random
            # control, which has no signal. Named rather than dropped.
            any_rows = True
            lines.append(f"| {m['model']} | no forecast to score | "
                         f"{len(settled):,} | - | "
                         f"{settled['won'].mean():.3f} | "
                         f"{_usd2(settled['cost_usd'].sum())} | "
                         f"{_usd2(settled['pnl_usd'].sum())} |")
    if not any_rows:
        lines.append("| — | no resolved position yet | 0 | - | - | - | - |")
    lines.append("")

    lines += [f"## Collector, last {GAP_WINDOW_DAYS} days", "",
              "| date | in-session cycles | runs | median gap | longest gap | gaps >2x | "
              "over cadence | not ok | failures | tokens missing |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    if gaps.empty:
        lines.append("| — | 0 | 0 | - | - | 0 | 0 | 0 | 0 | 0 |")
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

    runs = run_all.run_everything(dates, echo=echo)
    date, note = resolve_date(args.date, dates)
    if note:
        echo(f"  {note}")
    _, sample = sim.universe_scores(dates)
    since = (dt.date.fromisoformat(dates[-1]) - dt.timedelta(days=GAP_WINDOW_DAYS)).isoformat()
    gaps = collector_gaps(since)

    text = report(date, runs, dates, priced_stats, gaps, sample,
                  time.time() - started, note)
    sim.REPORTS.mkdir(parents=True, exist_ok=True)
    out = sim.REPORTS / f"daily_{date}.md"
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
