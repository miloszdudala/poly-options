"""Configs: one YAML file per pipeline, in `model/configs/`.

A config names one variant per step (`use:`) and that variant's parameters.
`extends: <name>` starts from another config and changes only what it lists,
so an extension reads as its difference from the MVP. Overrides on the command
line (`--set vol.use=strike_first_expiry_after`) change one value for one run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

CONFIGS = Path(__file__).resolve().parent.parent / "configs"
STEPS = ("venue", "markets", "inputs", "vol", "prob", "gates", "fee", "bar", "sizing", "fills", "account")


def find(name: str) -> Path:
    path = Path(name)
    if path.suffix in (".yaml", ".yml") and path.exists():
        return path
    for folder in (CONFIGS, CONFIGS / "extensions"):
        candidate = folder / f"{name}.yaml"
        if candidate.exists():
            return candidate
    known = sorted(p.stem for p in CONFIGS.rglob("*.yaml"))
    raise FileNotFoundError(f"no config {name!r}; known: {', '.join(known)}")


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and "use" not in value:
            out[key] = _merge(out[key], value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict) \
                and value.get("use") == out[key].get("use"):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(name: str, overrides: list[str] | None = None) -> dict:
    """The config `name` (a file stem or a path), with `extends` resolved and
    `overrides` ("step.key=value", value read as YAML) applied."""
    path = find(name)
    raw = yaml.safe_load(path.read_text()) or {}
    parent = raw.pop("extends", None)
    config = _merge(load(parent), raw) if parent else raw
    config["name"] = raw.get("name", path.stem)
    for item in overrides or []:
        key, _, value = item.partition("=")
        *parents, leaf = key.split(".")
        node = config
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = yaml.safe_load(value)
    if overrides:
        config["name"] = config["name"] + "+" + ",".join(overrides)
    missing = [s for s in STEPS if s not in config]
    if missing:
        raise ValueError(f"config {config['name']!r} has no {', '.join(missing)}")
    return config


def digest(config: dict, steps) -> str:
    """A short hash of the given steps' settings."""
    blob = json.dumps({s: config[s] for s in steps}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
