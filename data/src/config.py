"""Paths and YAML configuration loading."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

# .../poly-options/data/src/config.py -> .../poly-options/data
DATA_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = DATA_DIR.parent
CONFIG_DIR = DATA_DIR / "config"
STORE_DIR = DATA_DIR / "store"
RAW_DIR = STORE_DIR / "raw"
PARQUET_DIR = STORE_DIR / "parquet"
META_DIR = STORE_DIR / "meta"
LOG_DIR = DATA_DIR / "logs"


@functools.lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name) as fh:
        return yaml.safe_load(fh)


def markets_cfg() -> dict:
    return load_yaml("markets.yaml")


def underlyings_cfg() -> dict:
    return load_yaml("underlyings.yaml")


def ensure_dirs() -> None:
    for d in (RAW_DIR, PARQUET_DIR, META_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


class Underlying:
    """One tracked symbol, with its per-source symbol spellings."""

    def __init__(self, symbol: str, tier: str, overrides: dict):
        self.symbol = symbol
        self.tier = tier
        o = overrides.get(symbol, {}) or {}
        self.cboe_symbol = o.get("cboe_symbol", symbol)
        self.yf_symbol = o.get("yf_symbol", symbol)
        self.is_index = bool(o.get("is_index", False))

    def __repr__(self) -> str:
        return f"Underlying({self.symbol}, tier={self.tier})"


def underlyings(with_options_only: bool = False) -> list[Underlying]:
    cfg = underlyings_cfg()
    overrides = cfg.get("overrides", {}) or {}
    out: list[Underlying] = []
    seen: set[str] = set()
    for tier, spec in cfg["tiers"].items():
        if with_options_only and not spec.get("options", True):
            continue
        for name in spec["names"]:
            if name in seen:
                continue
            seen.add(name)
            out.append(Underlying(name, tier, overrides))
    return out
