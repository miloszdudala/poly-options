"""Quick health check: counts per table for the latest cycle, plus one book.

    python data/inspect.py [--cycle CYCLE_ID]

Deliberately lives outside data/src/: a module named inspect.py shadows the
standard library module for every sibling that imports it (numpy and duckdb
both do), which would break the collectors if it sat next to them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# This file is deliberately named inspect.py, which shadows the standard library
# module of the same name for anything imported afterwards (duckdb imports it via
# importlib.metadata). Drop our own directory from the front of sys.path so the
# stdlib wins, then put it back for the local imports below.
_HERE = os.path.dirname(os.path.abspath(__file__))
_removed = [p for p in list(sys.path) if os.path.abspath(p or os.getcwd()) == _HERE]
for p in _removed:
    sys.path.remove(p)

import duckdb  # noqa: E402

sys.path.insert(0, os.path.join(_HERE, "src"))

from config import PARQUET_DIR  # noqa: E402

TABLES = [
    "runs", "poly_markets", "poly_books", "poly_price_history",
    "poly_unparsed", "kalshi_markets", "kalshi_books", "option_quotes",
    "underlying_quotes", "underlying_bars", "rates", "dividends", "earnings",
    "resolutions", "failures",
]


def src(table: str) -> str | None:
    root = PARQUET_DIR / table
    if not root.exists() or not any(root.rglob("*.parquet")):
        return None
    return f"read_parquet('{root}/**/*.parquet', hive_partitioning=true, union_by_name=true)"


def latest_cycle(con) -> str | None:
    runs = src("runs")
    if runs:
        got = con.sql(f"select cycle_id from {runs} order by started_at_utc desc limit 1").fetchall()
        if got:
            return got[0][0]
    for table in ("poly_books", "poly_markets"):
        rel = src(table)
        if rel:
            got = con.sql(f"select max(cycle_id) from {rel}").fetchall()
            if got and got[0][0]:
                return got[0][0]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", help="cycle id (default: most recent)")
    args = parser.parse_args()

    con = duckdb.connect()
    cycle = args.cycle or latest_cycle(con)
    if not cycle:
        print("No data yet. Run:  python data/src/jobs.py snapshot")
        return 1

    print(f"\n=== latest cycle: {cycle} ===\n")

    runs = src("runs")
    if runs:
        df = con.sql(f"""select job, started_at_utc, round(duration_seconds,1) secs,
                         status, failure_count, counts_json
                         from {runs} where cycle_id = '{cycle}'""").df()
        if len(df):
            row = df.iloc[0]
            print(f"job={row['job']}  status={row['status']}  "
                  f"{row['secs']}s  failures={row['failure_count']}")
            try:
                for k, v in sorted(json.loads(row["counts_json"]).items()):
                    print(f"    {k:<26} {v}")
            except (ValueError, TypeError):
                pass
            print()

    print(f"{'table':<22}{'rows (cycle)':>14}{'rows (all)':>14}")
    print("-" * 50)
    for table in TABLES:
        rel = src(table)
        if not rel:
            print(f"{table:<22}{'-':>14}{'-':>14}")
            continue
        total = con.sql(f"select count(*) from {rel}").fetchone()[0]
        try:
            cyc = con.sql(f"select count(*) from {rel} where cycle_id='{cycle}'").fetchone()[0]
        except duckdb.Error:
            cyc = 0
        print(f"{table:<22}{cyc:>14,}{total:>14,}")

    books = src("poly_books")
    if books:
        # A `daily` run writes no books, so fall back to the most recent cycle
        # that actually has one rather than showing nothing.
        book_cycle = cycle
        has = con.sql(f"select count(*) from {books} where cycle_id='{cycle}'").fetchone()[0]
        if not has:
            got = con.sql(f"select max(cycle_id) from {books}").fetchone()
            book_cycle = got[0] if got and got[0] else cycle
        pick = con.sql(f"""
            select token_id, any_value(ticker) t, any_value(contract_type) ct,
                   any_value(coalesce(strike, barrier)) lvl, count(*) n
            from {books} where cycle_id='{book_cycle}'
            group by token_id having count(*) > 6
            order by n desc limit 1""").df()
        if len(pick):
            row = pick.iloc[0]
            suffix = "" if book_cycle == cycle else f"  [from cycle {book_cycle}]"
            print(f"\n=== example order book: {row['t']} {row['ct']} "
                  f"level={row['lvl']} (token {row['token_id'][:16]}...){suffix} ===")
            df = con.sql(f"""
                select side, depth_rank, price, size, round(price*size,2) as notional_usd
                from {books}
                where cycle_id='{book_cycle}' and token_id='{row['token_id']}'
                order by side, depth_rank""").df()
            print(df.to_string(index=False))
            top = con.sql(f"""
                select max(case when side='bid' then price end) best_bid,
                       min(case when side='ask' then price end) best_ask
                from {books} where cycle_id='{book_cycle}' and token_id='{row['token_id']}'
                """).df().iloc[0]
            print(f"\nbest bid {top['best_bid']}  best ask {top['best_ask']}")

    fails = src("failures")
    if fails:
        df = con.sql(f"""select venue, error, count(*) n from {fails}
                         where cycle_id='{cycle}' group by 1,2 order by n desc limit 10""").df()
        if len(df):
            print("\n=== failures this cycle ===")
            print(df.to_string(index=False))

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
