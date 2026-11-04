"""Explicit Arrow schema for the engine's output, and the writer.

Named `fair_schema`, not `schemas`, because `data/src/schemas.py` already
claims that name. Both directories are put on `sys.path` as flat module trees,
so two modules called `schemas` cannot coexist in one process: whichever lands
first wins for everything, and the loser fails at its one distinctive call -
`storage.coerce` for the collector, `write_fair` for the engine. Running both
test suites in one pytest process is enough to trigger it.

Same reason as `data/src/schemas.py`: DuckDB's `read_parquet` glob errors
outright when one file in the tree has a column its neighbours lack, so a
column whose type or presence drifts between partitions makes the whole
history unreadable. Declaring it once keeps every partition union-compatible.

Adding a column here is a breaking change unless the existing partitions are
rewritten with it as nulls (README, "Query").
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa

from ledger_schema import write_rows

STORE = Path(__file__).resolve().parent / "store"

S = pa.string()
F = pa.float64()
I = pa.int64()
B = pa.bool_()

FAIR = pa.schema([pa.field(n, t) for n, t in [
    ("cycle_id", S), ("computed_at_utc", S),
    # what it is a fair value of
    ("market_id", S), ("ticker", S), ("contract_type", S), ("direction", S),
    ("barrier", F), ("period", S), ("end_date", S), ("deadline_utc", S),
    # the fair (P of a touch) and its spot sensitivity
    ("fair", F), ("delta", F),
    # the inputs, so any row can be recomputed by hand
    ("sigma_atm", F), ("spot", F), ("spot_source", S), ("rate", F),
    ("T_sessions", F), ("T_years", F), ("expiry", S), ("n_strikes", I),
    # the reasons not to believe it
    ("quote_age_s", F), ("book_age_s", F), ("book_timestamp_utc", S),
    ("quote_time_utc", S),
    ("deep_otm", B), ("clamped_atm", B), ("exact_expiry", B), ("no_chain", B),
    ("stale_book", B), ("earnings_inside", B), ("earnings_gap", B),
    ("touch_pending", B), ("flags", S),
]])


def write_fair(rows, date: str, table: str = "fair") -> Path | None:
    """Append one Parquet file to model/store/fair/date=YYYY-MM-DD/.

    One file per call, append-only, exactly like the collector's `write_table`:
    it keeps writes atomic and lets DuckDB read the whole tree with a glob.
    """
    import datetime as dt

    rows = list(rows)
    if not rows:
        return None
    out_dir = STORE / table / f"date={date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S%f")
    return write_rows(rows, FAIR, out_dir / f"part-{stamp}.parquet")
