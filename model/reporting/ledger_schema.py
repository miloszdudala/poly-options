"""Explicit Arrow schemas for what a simulated model did, and the writer.

Named `ledger_schema`, not `schemas`, for the reason P2 recorded: `data/src`
already owns that module name and both directories are flat trees on
`sys.path`, so two modules called `schemas` cannot coexist in one process.

Two tables per run (one model x type x account), each in its own directory,
because DuckDB's `read_parquet` glob errors when one file in the tree has a
column its neighbours lack:

* `fills` - every level bought, at what price, with what fee, and the EV it
            promised at entry;
* `bets`  - one row per bet (market and side): its cost, the EV it promised,
            and, once it resolved, what it paid.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

STORE = Path(__file__).resolve().parent.parent / "store"

S = pa.string()
F = pa.float64()
I = pa.int64()
B = pa.bool_()

COMMON = [
    ("run_id", S), ("model", S), ("type", S), ("account", S),
    ("market_id", S), ("side", S), ("outcome_index", I), ("ticker", S),
    ("contract_type", S), ("level", F), ("deadline_utc", S), ("period", S),
]

FILLS = pa.schema([pa.field(n, t) for n, t in COMMON + [
    ("cycle_id", S), ("filled_at_utc", S), ("depth_rank", I), ("price", F),
    ("shares", F), ("fee_usd", F), ("cost_usd", F), ("ev_usd", F),
    ("win_prob", F), ("level_size", F),
]])

BETS = pa.schema([pa.field(n, t) for n, t in COMMON + [
    ("direction", S), ("vol", S), ("entry_cycle_id", S), ("entry_at_utc", S),
    ("win_prob", F), ("shares", F), ("cost_usd", F), ("fee_usd", F), ("ev_usd", F),
    ("result", S), ("payout_usd", F), ("pnl_usd", F), ("settled_at_utc", S),
]])

TABLES = {"fills": FILLS, "bets": BETS}


def _cast(value, arrow_type):
    if value is None:
        return None
    if arrow_type == S:
        text = str(value)
        return text if text not in ("", "NaT", "nan", "None") else None
    if arrow_type in (F, I):
        try:
            return float(value) if arrow_type == F else int(value)
        except (TypeError, ValueError):
            return None
    if arrow_type == B:
        return bool(value)
    return value


def write_rows(rows: list[dict], schema: pa.Schema, path: Path) -> Path:
    """Write `rows` to `path` under `schema`, casting each value to its column's
    type and dropping keys the schema does not name. Shared with `fair_schema`."""
    typed = [{f.name: _cast(row.get(f.name), f.type) for f in schema} for row in rows]
    pq.write_table(pa.Table.from_pylist(typed, schema=schema), path, compression="zstd")
    return path


def write_ledger(rows, table: str, name: str) -> Path | None:
    """One Parquet file per (run, table) under model/store/ledger/<name>/<table>/.

    Not append-only, unlike the fair store: a ledger is the output of one run of
    one model over one window, and two runs of the same window are the same
    experiment repeated, not more data. The run's own directory is cleared
    first so a shortened re-run cannot leave a longer run's tail behind it.
    """
    schema = TABLES[table]
    rows = list(rows)
    out_dir = STORE / "ledger" / name / table
    if out_dir.exists():
        shutil.rmtree(out_dir)
    if not rows:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S%f")
    return write_rows(rows, schema, out_dir / f"part-{stamp}.parquet")
