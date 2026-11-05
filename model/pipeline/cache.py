"""The pricing steps' output, kept on disk per cycle so a replay does not
reprice what it priced before.

Keyed by everything that could change the answer: the pricing steps'
settings in the config, the source code of the pricing modules, and the store
being read. Change any of them - a vol variant, a gate parameter, a line in
`prob.py`, a sample instead of the full store - and the key changes, so a
stale price can never be read back. Files live in
`model/store/cache/<key>/<date>/<cycle>.parquet`, beside a copy of the
settings that made them.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import yaml

import store
from pipeline import config as configs
from pipeline.core import PRICING_STEPS, Cycle, price

ROOT = Path(__file__).resolve().parent.parent / "store" / "cache"
SOURCES = ("core.py", "venues.py", "markets.py", "inputs.py", "vol.py", "prob.py", "gates.py",
           "maths.py", "wti.py",
           "../store.py", "../sessions.py")


def key(config: dict) -> str:
    here = Path(__file__).resolve().parent
    code = hashlib.sha1(b"".join((here / s).read_bytes() for s in SOURCES)).hexdigest()
    return hashlib.sha1(f"{configs.digest(config, ('venue',) + PRICING_STEPS)}|{code}|{store.ROOT}"
                        .encode()).hexdigest()[:12]


def priced(cycle: Cycle, config: dict, use_cache: bool = True) -> pd.DataFrame:
    """The cycle's priced markets: from the cache if there, else priced now
    and kept. Raises `core.NotReady` (and caches nothing) when the cycle cannot
    be priced yet."""
    if not use_cache:
        return price(cycle, config)
    folder = ROOT / key(config)
    path = folder / cycle.date / f"{cycle.id}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    frame = price(cycle, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = folder / "config.yaml"
    if not settings.exists():
        settings.write_text(yaml.safe_dump({s: config[s] for s in ("venue",) + PRICING_STEPS}
                                           | {"store": str(store.ROOT)}, sort_keys=False))
    frame.to_parquet(path, index=False)
    return frame
