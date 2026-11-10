"""Replay ba48f7b's edge_taker on weekly hit markets with delayed (base) or
next-cycle (rt) option chains, on the same cycles: each day's last cycle is
dropped from both because rt has no next chain for it.

Usage: python cmp_run.py <worktree> <base|rt> <dates,...>
"""
import sys
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
dates = sys.argv[3].split(",")
sys.path.insert(0, str(wt / "model"))
sys.path.append(str(wt / "data" / "src"))

import data  # noqa: E402
import sim  # noqa: E402
import run_all  # noqa: E402
from models import EdgeTaker  # noqa: E402

if mode == "rt":
    data.FAIR_STORE = wt / "model" / "store_rt" / "fair"


class CmpLoader(sim.Loader):
    def _fair(self, date):
        if date not in self._fair_by_date:
            frame = data.fair_values(date)
            if len(frame):
                frame = frame[frame["period"] == "weekly"]
            self._fair_by_date[date] = frame
        return self._fair_by_date[date]

    def cycles(self):
        if self._cycles is None:
            out = []
            for date in self.dates:
                sess = data.session_cycles(date)[:-1]
                priced = set(self._fair(date)["cycle_id"].unique()) if len(self._fair(date)) else set()
                out += [c for c in sess if c in priced]
            self._cycles = sorted(out)
        return self._cycles


et = EdgeTaker()
et.name = f"edge_taker_cmp_{mode}"
res, m = run_all.run_one(et, dates, lambda: CmpLoader(dates))
print(mode, m["cycles"], m["positions_opened"], m["filled_usd"], m.get("pnl_usd"), flush=True)
