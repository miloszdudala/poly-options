"""Set proof/BETS.md against proof/SETTLEMENTS.md: what was promised, what came in.

    python model/review_proof.py [proof/]

Standard library only, and it reads nothing but the two files, so anyone with
a copy of them can run it. Bets and settlements are joined on the market ID.
Money is compared on settled bets only: a bet still open has promised EV but
no result yet.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path


def rows(path: Path) -> list[list[str]]:
    """A table's rows; each starts with its 20xx write time."""
    return [[cell.strip() for cell in line.split("|")[1:-1]]
            for line in path.read_text().splitlines() if line.startswith("| 20")]


def review(folder: Path) -> str:
    settled = {r[2]: r for r in rows(folder / "SETTLEMENTS.md")}
    bets = rows(folder / "BETS.md")
    by_day: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0])
    still_open = []
    for bet in bets:
        market_id, cost, promised = bet[3], float(bet[8]), float(bet[10])
        if market_id not in settled:
            still_open.append(bet)
            continue
        pnl = float(settled[market_id][5])
        day = by_day[bet[0][:10]]
        day[0] += 1
        day[1] += pnl > 0
        day[2] += cost
        day[3] += promised
        day[4] += pnl
    lines = [f"{'written':<10} {'bets':>5} {'won':>5} {'cost $':>10} {'promised $':>11} "
             f"{'PnL $':>10} {'PnL/promised':>13}"]
    total = [0, 0, 0.0, 0.0, 0.0]
    for day, (n, won, cost, promised, pnl) in sorted(by_day.items()):
        lines.append(_line(day, n, won, cost, promised, pnl))
        total = [a + b for a, b in zip(total, (n, won, cost, promised, pnl))]
    lines.append(_line("all", *total))
    lines.append(f"\n{len(still_open)} bet(s) open: ${sum(float(b[8]) for b in still_open):,.2f} in, "
                 f"${sum(float(b[10]) for b in still_open):,.2f} promised")
    orphans = sorted(set(settled) - {b[3] for b in bets})
    if orphans:
        lines.append(f"settlements with no bet: {' '.join(orphans)}")
    return "\n".join(lines)


def _line(label, n, won, cost, promised, pnl) -> str:
    ratio = f"{pnl / promised:.2f}" if promised else "-"
    return (f"{label:<10} {n:>5} {won:>5} {cost:>10,.2f} {promised:>11,.2f} "
            f"{pnl:>10,.2f} {ratio:>13}")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent.parent / "proof"
    print(review(Path(sys.argv[1]) if len(sys.argv) > 1 else here))
