"""Settings and secrets for the live trader.

Secrets come from the environment or from `live/.env` (gitignored), never from
`config.yaml`. The `.env` reader is ten lines rather than a dependency.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.yaml"
ENV_FILE = HERE / ".env"
STORE = HERE / "store"
# `touch live/STOP` halts every run before it does anything, live or dry.
KILL_SWITCH = HERE / "STOP"


@dataclass(frozen=True)
class Settings:
    armed: bool
    bankroll_usd: float
    cap_per_market_usd: float
    daily_spend_usd: float
    max_orders_per_cycle: int
    min_ev: float
    min_roi: float
    high_price: float
    high_price_min_roi: float
    high_price_min_ev: float
    yes_premium: float
    take_fraction: float
    fee_model: str
    fee_rate: float
    loop_seconds: float
    max_cycle_age_minutes: float
    max_spot_move: float
    collect_if_older_than_minutes: float


def load(path: Path = CONFIG) -> Settings:
    raw = yaml.safe_load(path.read_text()) or {}
    names = {f.name for f in fields(Settings)}
    missing = names - raw.keys()
    unknown = raw.keys() - names
    if missing or unknown:
        # A typo in a cap must not silently fall back to some default.
        raise ValueError(f"{path.name}: missing {sorted(missing)}, unknown {sorted(unknown)}")
    if raw["fee_model"] not in ("conservative", "venue"):
        raise ValueError(f"fee_model must be conservative or venue, got {raw['fee_model']!r}")
    return Settings(**raw)


def secrets() -> dict[str, str]:
    """POLYMARKET_PRIVATE_KEY (required to trade) and POLYMARKET_WALLET_ADDRESS
    (optional: omitted, the SDK acts for the key's own Deposit Wallet)."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return {k: v for k, v in values.items() if v}
