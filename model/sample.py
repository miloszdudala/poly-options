"""Cut a small store out of the full one, to try a step on in seconds.

    run.py sample --from 2026-10-05 --to 2026-10-09 --tickers NVDA,PLTR --out model/samples/oct-w1
    run.py sample --synthetic --out model/samples/synthetic
    run.py --store model/samples/oct-w1 lab score --vary vol.use=...

Copies, with the same layout: every per-cycle table of the dates (books,
chains, quotes, runs), the tickers' bars over the dates and `--history-days`
before them (the touch check reads back to the period start), and every
market row, resolution, earnings date and rate the tickers need, whatever
its date (a market settles after the window). SPY's bars always come along:
the bar guard probes them. `--synthetic` writes the made-up store of
`synthetic.py` instead.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import store

PER_CYCLE = {"poly_books": "ticker", "option_quotes": "underlying",
             "underlying_quotes": "symbol", "runs": None}
BY_TICKER = {"poly_markets": "ticker", "resolutions": "ticker", "earnings": "symbol",
             "dividends": "symbol", "rates": None}


def cut(start: str, end: str, tickers: list[str], out: Path, history_days: int = 40) -> Path:
    out = Path(out)
    symbols = sorted(set(tickers) | {"SPY"})
    quoted = ", ".join(f"'{t}'" for t in symbols)
    since = (dt.date.fromisoformat(start) - dt.timedelta(days=history_days)).isoformat()

    def copy(table: str, where: str) -> None:
        if not (store.ROOT / table).exists():
            return
        target = out / table
        out.mkdir(parents=True, exist_ok=True)
        store._con().sql(f"""
            copy (select * from {store._src(table)} where {where})
            to '{target}' (format parquet, partition_by (date), overwrite_or_ignore true)
        """)

    for table, column in PER_CYCLE.items():
        dates = f"date between '{start}' and '{end}'"
        copy(table, dates if column is None else f"{dates} and {column} in ({quoted})")
    copy("underlying_bars", f"symbol in ({quoted}) and bar_time_utc >= '{since}' "
                            f"and bar_time_utc <= '{end}T23:59:59Z' or "
                            f"(symbol in ({quoted}) and interval = '1d')")
    for table, column in BY_TICKER.items():
        copy(table, "true" if column is None else f"{column} in ({quoted})")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run.py sample", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--tickers", default="", help="comma-separated")
    parser.add_argument("--history-days", type=int, default=40)
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    if args.synthetic:
        import synthetic
        print(synthetic.build(Path(args.out)))
        return 0
    if not (args.start and args.end and args.tickers):
        parser.error("--from, --to and --tickers are needed (or --synthetic)")
    print(cut(args.start, args.end, args.tickers.split(","), Path(args.out), args.history_days))
    return 0
