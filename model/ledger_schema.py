"""Explicit Arrow schemas for what a simulated model did, and the writer.

Named `ledger_schema`, not `schemas`, for the reason P2 recorded: `data/src`
already owns that module name and both directories are flat trees on
`sys.path`, so two modules called `schemas` cannot coexist in one process.

Three tables, each in its own directory, because DuckDB's `read_parquet` glob
errors when one file in the tree has a column its neighbours lack - so fills,
marks and resolutions cannot share a directory even though they share a run.

* `fills`     - every share bought, at what price, with what fee, and whether
                the level was still there at the next cycle (the persistence
                check of MODELS 4.2).
* `marks`     - one row per position per cycle: the model's fair, the
                liquidation bid, and the beta-dollar-delta it carried.
* `resolutions` - one row per position that settled: what it paid, when the
                cash came back, and the capital-days it consumed.
"""
from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

STORE = Path(__file__).resolve().parent / "store"

S = pa.string()
F = pa.float64()
I = pa.int64()
B = pa.bool_()

FILLS = pa.schema([pa.field(n, t) for n, t in [
    ("run_id", S), ("model", S), ("cycle_id", S), ("filled_at_utc", S),
    ("market_id", S), ("outcome_index", I), ("ticker", S), ("contract_type", S),
    ("barrier", F), ("deadline_utc", S), ("period", S),
    ("depth_rank", I), ("price", F), ("shares", F), ("fee_usd", F), ("cost_usd", F),
    ("limit_price", F), ("fair", F), ("fair_source", S), ("reason", S),
    # the persistence check: None until the next cycle has been seen
    ("confirmed", B), ("checked", B), ("level_size", F), ("next_level_size", F),
]])

MARKS = pa.schema([pa.field(n, t) for n, t in [
    ("run_id", S), ("model", S), ("cycle_id", S), ("marked_at_utc", S),
    ("market_id", S), ("outcome_index", I), ("ticker", S), ("contract_type", S),
    ("shares", F), ("cost_usd", F), ("fair", F), ("mark_fair_usd", F),
    ("bid", F), ("mark_bid_usd", F),
    ("delta", F), ("spot", F), ("beta", F), ("beta_dollar_delta", F),
]])

RESOLUTIONS = pa.schema([pa.field(n, t) for n, t in [
    ("run_id", S), ("model", S), ("market_id", S), ("outcome_index", I),
    ("ticker", S), ("contract_type", S), ("barrier", F),
    ("entry_cycle_id", S), ("entry_at_utc", S), ("settled_at_utc", S),
    ("cash_back_at_utc", S), ("result", S), ("payout_per_share", F),
    ("shares", F), ("cost_usd", F), ("fee_usd", F), ("payout_usd", F), ("pnl_usd", F),
    ("shares_confirmed", F), ("cost_confirmed_usd", F), ("payout_confirmed_usd", F),
    ("pnl_confirmed_usd", F),
    ("entry_fair", F), ("entry_fair_engine", F), ("fair_source", S),
    ("capital_days", F),
]])

TABLES = {"fills": FILLS, "marks": MARKS, "resolutions": RESOLUTIONS}


def _cast(value, arrow_type):
    if value is None:
        return None
    if arrow_type == S:
        text = str(value)
        return text if text not in ("", "NaT", "nan", "None") else None
    if arrow_type == F:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if arrow_type == I:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if arrow_type == B:
        return bool(value)
    return value


def write_ledger(rows, table: str, model: str, run_id: str) -> Path | None:
    """One Parquet file per (run, table) under model/store/ledger/<model>/<table>/.

    Not append-only, unlike the fair store: a ledger is the output of one run of
    one model over one window, and two runs of the same window are the same
    experiment repeated, not more data. The run's own directory is cleared
    first so a shortened re-run cannot leave a longer run's tail behind it.
    """
    schema = TABLES[table]
    rows = list(rows)
    out_dir = STORE / "ledger" / model / table
    if out_dir.exists():
        shutil.rmtree(out_dir)
    if not rows:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S%f")
    path = out_dir / f"part-{stamp}.parquet"
    typed = [{f.name: _cast(row.get(f.name), f.type) for f in schema} for row in rows]
    pq.write_table(pa.Table.from_pylist(typed, schema=schema), path, compression="zstd")
    return path
