"""Raw gzipped JSON archive and normalised Parquet tables."""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from config import META_DIR, PARQUET_DIR, RAW_DIR
from schemas import coerce
from util import get_logger, now_utc, utc_iso

log = get_logger("storage")

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name: str) -> str:
    return _SAFE.sub("_", str(name))[:120]


def raw_enabled(kind: str) -> bool:
    """Whether this class of raw archive is switched on in markets.yaml."""
    from config import markets_cfg

    cfg = markets_cfg().get("raw", {}) or {}
    return bool(cfg.get(f"archive_{kind}", False))


def write_raw(venue: str, identifier: str, payload: Any, cycle_id: str) -> Path:
    """Archive a response verbatim: store/raw/{venue}/{date}/{cycle}_{id}.json.gz"""
    day = now_utc().strftime("%Y-%m-%d")
    out_dir = RAW_DIR / venue / day
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{cycle_id}_{_safe(identifier)}.json.gz"
    body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    if isinstance(body, str):
        body = body.encode()
    with gzip.open(path, "wb") as fh:
        fh.write(body)
    return path


def _cycle_day(rows: Sequence[dict]) -> str | None:
    """The UTC date a cycle *started*, read back out of its cycle_id.

    The partition used to default to the write clock, which silently split one
    cycle across two `date=` partitions whenever a run crossed UTC midnight -
    not a corner case on a laptop, where the 2026-09-17 daily job ran from
    21:32Z to past 01:44Z and landed poly_markets under date=2026-09-17 and
    poly_price_history from the same cycle_id under date=2026-09-18. Filtering
    a day's partition then drops rows without saying so.
    """
    for row in rows:
        cycle_id = row.get("cycle_id")
        if isinstance(cycle_id, str) and len(cycle_id) >= 8 and cycle_id[:8].isdigit():
            return f"{cycle_id[:4]}-{cycle_id[4:6]}-{cycle_id[6:8]}"
    return None


def write_table(table: str, rows: Sequence[dict], partition_date: str | None = None) -> Path | None:
    """Append rows to store/parquet/{table}/date=YYYY-MM-DD/part-*.parquet

    One file per call keeps writes atomic and append-only; DuckDB reads the
    whole tree with a glob and picks up `date` as a partition column. The
    partition is the cycle's start date, so every row of one cycle_id lands
    together however long the run takes.
    """
    if not rows:
        return None
    day = partition_date or _cycle_day(rows) or now_utc().strftime("%Y-%m-%d")
    out_dir = PARQUET_DIR / table / f"date={day}"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%H%M%S%f")
    path = out_dir / f"part-{stamp}.parquet"
    typed, schema = coerce(table, list(rows))
    if schema is not None:
        arrow = pa.Table.from_pylist(typed, schema=schema)
    else:
        arrow = pa.Table.from_pylist(_stringify(typed))
    pq.write_table(arrow, path, compression="zstd")
    log.info("wrote %-20s rows=%-6d -> %s", table, len(rows), path.name)
    return path


def _stringify(rows: Sequence[dict]) -> list[dict]:
    """Normalise nested values so Arrow never has to guess a struct schema.

    Every collector emits flat rows; anything nested (a list of tags, a raw
    sub-object) is stored as a JSON string so the column type stays stable
    across partitions.
    """
    out = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if isinstance(value, (dict, list, tuple)):
                clean[key] = json.dumps(value)
            else:
                clean[key] = value
        out.append(clean)
    return out


def write_raw_once(venue: str, identifier: str, payload: Any, cycle_id: str) -> Path | None:
    """Archive a payload only the first time we see it, or when it changes.

    Used for market rules and metadata: the venue's own description of a bet does
    not change from cycle to cycle, so storing it every 15 minutes is pure waste,
    but losing it entirely would leave nothing to check a disputed settlement
    against.
    """
    import hashlib

    body = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    seen_path = META_DIR / "raw_once_seen.json"
    META_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(seen_path) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    key = f"{venue}/{_safe(identifier)}"
    if seen.get(key) == digest:
        return None
    path = write_raw(venue, identifier, body, cycle_id)
    seen[key] = digest
    with open(seen_path, "w") as fh:
        json.dump(seen, fh)
    return path


def write_meta(name: str, payload: Any) -> Path:
    """Metadata / rules text that is refreshed in place rather than appended."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    path = META_DIR / f"{_safe(name)}.json"
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    return path


def read_meta(name: str) -> Any | None:
    path = META_DIR / f"{_safe(name)}.json"
    if not path.exists():
        return None
    with open(path) as fh:
        return json.load(fh)


def write_run_summary(row: dict) -> None:
    write_table("runs", [row])


def write_failures(failures: Sequence[dict], cycle_id: str) -> None:
    """Persist every failed request so gaps are visible later."""
    if not failures:
        return
    rows = [dict(f, cycle_id=cycle_id, fetched_at_utc=utc_iso()) for f in failures]
    write_table("failures", rows)
