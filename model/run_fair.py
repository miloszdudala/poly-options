"""Price every session cycle of one date and store the result.

    .venv/bin/python model/run_fair.py --date 2026-09-16

Prints markets priced, markets flagged by flag, the median quote age and the
runtime, then writes `model/store/fair/date=<date>/part-*.parquet`.

The quote-age line is the one to read first. It is `book_timestamp_utc` minus
`max(last_trade_time_utc)`, and it should land near 15 minutes: that is the
Cboe delay the README measures, and a number far from it means either the chain
or the book is not what it claims to be.
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
import fair_schema  # noqa: E402
from engine import price_cycle  # noqa: E402
from fair import Flags  # noqa: E402



def run_date(date: str, write: bool = True) -> tuple[pd.DataFrame, list[dict]]:
    """Recompute a whole date, replacing whatever was stored for it.

    The partition is cleared first. `write_fair` appends a part file per cycle,
    so a second run would otherwise double every market-cycle in the date and
    every downstream median would move without anything being wrong upstream.
    """
    cycles = data.session_cycles(date)
    if write:
        partition = fair_schema.STORE / "fair" / f"date={date}"
        if partition.exists():
            for old_part in partition.glob("*.parquet"):
                old_part.unlink()
            print(f"  cleared the existing {date} partition", file=sys.stderr)
    frames, stats = [], []
    for cycle_id in cycles:
        frame, stat = price_cycle(cycle_id)
        if len(frame):
            frames.append(frame)
            if write:
                fair_schema.write_fair(frame.to_dict("records"), date)
        stats.append(stat)
        print(f"  {cycle_id}  {stat['markets']:4d} markets  {stat['priced']:4d} priced  "
              f"{stat['seconds']:5.1f}s", file=sys.stderr)
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), stats


def report(date: str, df: pd.DataFrame, stats: list[dict], elapsed: float) -> str:
    if df.empty:
        return f"{date}: no session cycles with books."
    priced = df["fair"].notna()
    lines = [
        f"{date}: {len(stats)} session cycles, {len(df):,} market-cycles, "
        f"{int(priced.sum()):,} priced ({priced.mean():.1%})",
        f"  markets per cycle      {df.groupby('cycle_id').size().median():.0f} median",
        f"  runtime                {elapsed:.1f}s total, "
        f"{np.median([s['seconds'] for s in stats]):.1f}s median per cycle",
        f"  median quote_age_s     {np.nanmedian(df['quote_age_s']):.0f} "
        f"({np.nanmedian(df['quote_age_s']) / 60:.1f} min)",
        f"  median book_age_s      {np.nanmedian(df['book_age_s']):.0f}",
        f"  IV vs Cboe             median gap "
        f"{np.nanmedian([s['iv_gap_median'] for s in stats]):.4f}, "
        f"{sum(s['iv_disagreements'] for s in stats):,} of "
        f"{sum(s['iv_checked'] for s in stats):,} liquid strikes past 0.01",
        "",
        "  flag              market-cycles     share",
    ]
    for flag in Flags.names():
        n = int(df[flag].fillna(False).astype(bool).sum())
        lines.append(f"  {flag:<18}{n:>10,}    {n / len(df):>7.1%}")
    lines += [
        "",
        f"  fair              median {df['fair'].median():.3f}, n {int(priced.sum()):,}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    started = time.time()
    df, stats = run_date(args.date, write=not args.no_write)
    print(report(args.date, df, stats, time.time() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
