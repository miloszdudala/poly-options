"""Run any model x type x account over any dates.

    .venv/bin/python model/run.py --model edge_taker --type poly-hit-weekly-stock \\
        --account compound --start 100 --from 2026-10-01 --to 2026-10-31

Defaults: every model, every type, account `max`, every date. Prints a short
result per model x type (bets, money in, promised EV, PnL, PnL by period) and
writes nothing unless `--save`. `run_all.py` and `daily.py` go through
`run_matrix` here.

Models are the classes in `model/models/*.py`, types the classes in
`model/types/*.py`; both are found by looking, so adding one is adding a file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import inspect
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see model/data.py

import bet_types  # noqa: E402
import data  # noqa: E402
import results  # noqa: E402
import sim  # noqa: E402
from models import Model  # noqa: E402

MODELS = HERE / "models"
TYPES = HERE / "types"
SKIP_FILES = {"__init__", "base"}


def _classes(folder: Path, base: type) -> dict[str, type]:
    """Every subclass of `base` with a `name`, defined in a file of `folder`.

    Loaded by path: `model/types` cannot be a package, because `model/` is on
    `sys.path` and a package called `types` would shadow the standard library.
    The folder goes on `sys.path` so a file can import its `_helper` siblings.
    """
    if str(folder) not in sys.path:
        sys.path.append(str(folder))
    found: dict[str, type] = {}
    for path in sorted(folder.glob("*.py")):
        if path.stem.startswith("_") or path.stem in SKIP_FILES:
            continue
        module_name = f"_{folder.name}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        for obj in vars(module).values():
            if (inspect.isclass(obj) and issubclass(obj, base) and obj is not base
                    and obj.__module__ == module_name and getattr(obj, "name", "")):
                if obj.name in found:
                    raise ValueError(f"two classes are called {obj.name!r}")
                found[obj.name] = obj
    return found


def discover_models(folder: Path = MODELS) -> dict[str, type]:
    return _classes(folder, Model)


def discover_types(folder: Path = TYPES) -> dict[str, type]:
    return _classes(folder, bet_types.Type)


def date_range(start: str | None, end: str | None) -> list[str]:
    """Every calendar date from `start` to `end`; a type replays only the
    cycles it has. Default: the first priced date to today."""
    if start is None:
        priced = data.fair_dates()
        start = priced[0] if priced else dt.date.today().isoformat()
    end = end or dt.date.today().isoformat()
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def run_matrix(dates: list[str], models: list[str] | None = None,
               types: list[str] | None = None, account: str = "max",
               start: float = 100.0, per_period: float | None = None,
               save: bool = False, tag: str = "", echo=None,
               model_classes: dict | None = None,
               type_classes: dict | None = None) -> list[tuple[sim.Result, dict]]:
    """Each model x type, separately, under a fresh account of its own."""
    model_classes = model_classes or discover_models()
    type_classes = type_classes or discover_types()
    for wanted, known, what in ((models, model_classes, "model"), (types, type_classes, "type")):
        unknown = sorted(set(wanted or []) - set(known))
        if unknown:
            raise SystemExit(f"unknown {what}: {', '.join(unknown)}; "
                             f"known: {', '.join(sorted(known))}")
    out = []
    for model_name in models or sorted(model_classes):
        for type_name in types or sorted(type_classes):
            result = sim.run(model_classes[model_name](), type_classes[type_name](),
                             sim.make_account(account, start, per_period), dates)
            m = results.summary(result)
            if save:
                results.write_run(result, tag, m)
            out.append((result, m))
            if echo:
                echo(f"  {model_name:<16} {type_name:<26} {m['bets']:5,} bets  "
                     f"${m['money_in']:10,.0f} in  {m['seconds']:6.1f}s")
    return out


def short_report(result: sim.Result, m: dict) -> str:
    lines = [f"{m['model']} on {m['type']} ({m['account']}): {m['bets']:,} bets, "
             f"${m['money_in']:,.2f} in, promised EV ${m['promised_ev']:,.2f}; "
             f"{m['settled']:,} settled: PnL ${m['pnl']:,.2f} against "
             f"${m['settled_promised_ev']:,.2f} promised"]
    table = results.by_period(result)
    for period, row in table.iterrows():
        lines.append(f"  {result.period} {period}: {int(row['bets']):4,} bets  "
                     f"${row['money_in']:9,.2f} in  promised ${row['promised_ev']:8,.2f}  "
                     f"PnL ${row['pnl']:9,.2f} on {int(row['settled']):,} settled")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", help="repeatable; default every model")
    parser.add_argument("--type", action="append", help="repeatable; default every type")
    parser.add_argument("--account", default="max", choices=["max", "compound", "budget"])
    parser.add_argument("--start", type=float, default=100.0,
                        help="starting cash of the compound account (default 100)")
    parser.add_argument("--per-period", type=float,
                        help="dollars per period of the budget account")
    parser.add_argument("--from", dest="start_date", help="first date, YYYY-MM-DD")
    parser.add_argument("--to", dest="end_date", help="last date, YYYY-MM-DD")
    parser.add_argument("--save", action="store_true", help="write ledgers and reports")
    parser.add_argument("--tag", default="", help="suffix for saved ledger and report names")
    args = parser.parse_args(argv)

    dates = date_range(args.start_date, args.end_date)
    runs = run_matrix(dates, args.model, args.type, args.account, args.start,
                      args.per_period, save=args.save, tag=args.tag)
    for result, m in runs:
        print(short_report(result, m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
