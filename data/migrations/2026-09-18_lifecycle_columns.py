"""One-off migration, 2026-09-18. Safe to re-run.

1. `poly_markets` gained created_at / start_date / closed_time / outcome_prices /
   result, and `resolutions` gained outcome_prices / created_at. DuckDB's glob
   refuses a tree whose files disagree on columns, so every existing partition
   is rewritten with the new columns. The values are recovered from the raw
   `events_matched` archive of the same cycle, which kept the fields the
   collector was dropping.
2. `option_quotes` rows written before commit dadb128 parsed Cboe's UTC file
   clock as Eastern, stamping source_quote_time_utc four hours late. Any row
   whose quote time is after its own fetch time is corrected by -4h.

    .venv/bin/python data/migrations/2026-09-18_lifecycle_columns.py
"""
from __future__ import annotations

import datetime as dt
import glob
import gzip
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from config import PARQUET_DIR, RAW_DIR  # noqa: E402
from poly_discover import _norm_list, resolution_result  # noqa: E402
from schemas import SCHEMAS, coerce  # noqa: E402


def raw_markets_by_cycle() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for path in glob.glob(str(RAW_DIR / "polymarket" / "*" / "*events_matched.json.gz")):
        cycle = os.path.basename(path).split("_events_matched")[0]
        events = json.load(gzip.open(path))
        out[cycle] = {str(m["id"]): m for e in events for m in e.get("markets") or []}
    return out


def rewrite(table: str, fill) -> None:
    schema = SCHEMAS[table]
    for path in sorted(glob.glob(str(PARQUET_DIR / table / "date=*" / "*.parquet"))):
        rows = pq.read_table(path).to_pylist()
        if not rows:
            continue
        changed = 0
        for row in rows:
            changed += bool(fill(row))
        typed, _ = coerce(table, rows)
        tmp = path + ".tmp"
        pq.write_table(pa.Table.from_pylist(typed, schema=schema), tmp, compression="zstd")
        os.replace(tmp, path)
        print(f"{table:14s} {os.path.relpath(path, PARQUET_DIR)}  rows={len(rows)} filled={changed}")


def main() -> None:
    raw = raw_markets_by_cycle()

    def fill_market(row: dict) -> bool:
        m = raw.get(row.get("cycle_id"), {}).get(str(row.get("market_id")))
        if row.get("outcome_prices") is not None or m is None:
            return False
        outcomes = _norm_list(m.get("outcomes"))
        row["created_at"] = m.get("createdAt")
        row["start_date"] = m.get("startDate") or m.get("startDateIso")
        row["closed_time"] = m.get("closedTime")
        row["outcome_prices"] = m.get("outcomePrices")
        row["result"] = resolution_result(m, outcomes)
        return True

    rewrite("poly_markets", fill_market)

    # Resolutions were only ever written by the daily job, whose cycle also
    # archived the raw events: same lookup.
    def fill_resolution(row: dict) -> bool:
        m = raw.get(row.get("cycle_id"), {}).get(str(row.get("market_id")))
        if row.get("result") is not None or m is None:
            return False
        from poly_books import _closed_time_iso
        outcomes = _norm_list(m.get("outcomes"))
        row["result"] = resolution_result(m, outcomes)
        row["settled_at_utc"] = _closed_time_iso(m.get("closedTime"))
        row["outcome_prices"] = m.get("outcomePrices")
        row["created_at"] = m.get("createdAt")
        return True

    rewrite("resolutions", fill_resolution)

    def fix_quote_time(row: dict) -> bool:
        q, f = row.get("source_quote_time_utc"), row.get("fetched_at_utc")
        if not q or not f or row.get("source") != "cboe":
            return False
        qt, ft = dt.datetime.fromisoformat(q), dt.datetime.fromisoformat(f)
        if qt <= ft + dt.timedelta(minutes=1):
            return False
        row["source_quote_time_utc"] = (qt - dt.timedelta(hours=4)).isoformat()
        return True

    rewrite("option_quotes", fix_quote_time)
    rewrite("underlying_quotes", fix_quote_time)


if __name__ == "__main__":
    main()
