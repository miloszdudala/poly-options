"""Weekly performance of the recorded bets, built from BETS.md and SETTLEMENTS.md alone.

    python proof/performance.py            # print the table
    python proof/performance.py --write    # save it as proof/PERFORMANCE.md

Standard library only, and it reads nothing but BETS.md and SETTLEMENTS.md, so
anyone with a copy of them can rebuild PERFORMANCE.md and check it.

A week is the bets whose market says "Week of ..." and share a deadline. Its
row appears once the deadline has passed and every one of those bets has
settled. Both files are only ever added to and no bet joins a week after its
deadline, so a row, once there, never changes: the table only grows.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = (
    "| Last settled (UTC) | Deadline (UTC) | Bets | Won | Lost | Expected wins | Cost $ "
    "| Promised EV $ | PnL $ | PnL / promised | Return on cost |\n"
    "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")


def rows(text: str) -> list[list[str]]:
    """A table's rows; each starts with its 20xx write time."""
    return [[cell.strip() for cell in line.split("|")[1:-1]]
            for line in text.splitlines() if line.startswith("| 20")]


def table(bets: str, settlements: str, now: datetime) -> str:
    """The whole table: one row per week that has fully resolved."""
    settled = {r[2]: r for r in rows(settlements)}
    weeks: dict[str, list[list[str]]] = defaultdict(list)
    for bet in rows(bets):
        if "Week of" in bet[4]:
            weeks[bet[2]].append(bet)
    out = [HEADER]
    for deadline, week in sorted(weeks.items()):
        passed = datetime.strptime(deadline, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) < now
        if not passed or any(b[3] not in settled for b in week):
            continue
        pnl = [float(settled[b[3]][5]) for b in week]
        cost = sum(float(b[8]) for b in week)
        promised = sum(float(b[10]) for b in week)
        won = sum(p > 0 for p in pnl)
        expected = sum(float(b[9].rstrip("%")) / 100 for b in week)
        cells = [max(settled[b[3]][0] for b in week), deadline, str(len(week)), str(won),
                 str(len(week) - won), f"{expected:.1f}", f"{cost:,.2f}", f"{promised:,.2f}",
                 f"{sum(pnl):,.2f}", f"{sum(pnl) / promised:.2f}" if promised else "-",
                 f"{sum(pnl) / cost:.1%}" if cost else "-"]
        out.append("| " + " | ".join(cells) + " |\n")
    return "".join(out)


def main() -> int:
    text = table((HERE / "BETS.md").read_text(), (HERE / "SETTLEMENTS.md").read_text(),
                 datetime.now(timezone.utc))
    if "--write" in sys.argv[1:]:
        (HERE / "PERFORMANCE.md").write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
