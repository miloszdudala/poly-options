"""The live trader's own record: append-only JSON lines under `live/store/`.

Three files, one line per event, never rewritten:

* `runs.jsonl`      - every run: when, which cycle, what it did, why it stopped;
* `orders.jsonl`    - every order, dry or live: the decision behind it (fair,
  EV, limit) and, for a live one, the venue's answer verbatim;
* `settled.jsonl`   - conditions redeemed by `trader.py redeem`, which is
  when a live position's cash is back in the wallet.

Dry and live share the files and are told apart by `mode`, and every
calculation below is per mode, so a dry week builds its own paper book that
the caps bind on exactly as they would on a live one. JSON lines rather than
Parquet because the volume is tiny and you will want to read them with `cat`.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from settings import STORE

EASTERN = ZoneInfo("America/New_York")


class Ledger:
    def __init__(self, root: Path = STORE):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # -- writing ----------------------------------------------------------

    def _append(self, name: str, row: dict) -> None:
        with (self.root / name).open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def run(self, row: dict) -> None:
        self._append("runs.jsonl", row)

    def order(self, row: dict) -> None:
        self._append("orders.jsonl", row)

    def settled(self, row: dict) -> None:
        self._append("settled.jsonl", row)

    # -- reading ----------------------------------------------------------

    def _read(self, name: str) -> list[dict]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def fills(self, mode: str) -> list[dict]:
        """Orders of this mode that bought something."""
        return [o for o in self._read("orders.jsonl")
                if o.get("mode") == mode and float(o.get("spent_usd") or 0) > 0]

    def held_markets(self, mode: str) -> set[str]:
        """Every market ever bought, on either side. One market, one entry."""
        return {o["market_id"] for o in self.fills(mode)}

    def settled_conditions(self) -> set[str]:
        return {r["condition_id"] for r in self._read("settled.jsonl")}

    def open_cost(self, mode: str, now: dt.datetime) -> float:
        """Dollars still at risk: paid for and not yet back.

        A live position stays open until it is redeemed, because the cash is
        not in the wallet until then. A dry one is released at its deadline,
        since there is nothing to redeem.
        """
        done = self.settled_conditions()
        total = 0.0
        for o in self.fills(mode):
            if o.get("condition_id") in done:
                continue
            if mode == "dry" and dt.datetime.fromisoformat(o["deadline_utc"]) <= now:
                continue
            total += float(o["spent_usd"])
        return total

    def spent_on(self, mode: str, session_date: dt.date) -> float:
        """Dollars bought during one US session date (Eastern)."""
        return sum(float(o["spent_usd"]) for o in self.fills(mode)
                   if dt.datetime.fromisoformat(o["at_utc"]).astimezone(EASTERN).date()
                   == session_date)
