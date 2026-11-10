"""Replay ba48f7b's edge_taker and random control on out-of-sample dates.

Usage: python oos_run.py <worktree> <universe: all|weekly> <dates,...>
Runs the old code unmodified; 'weekly' only narrows the fair frame (the
markets the models see) to period == 'weekly'.
"""
import random
import sys
from pathlib import Path

wt = Path(sys.argv[1]).resolve()
universe = sys.argv[2]
dates = sys.argv[3].split(",")
sys.path.insert(0, str(wt / "model"))
sys.path.append(str(wt / "data" / "src"))

import pandas as pd  # noqa: E402
import sim  # noqa: E402
import run_all  # noqa: E402
from models import EdgeTaker  # noqa: E402


class WeeklyLoader(sim.Loader):
    def _fair(self, date):
        frame = super()._fair(date)
        if len(frame) and universe == "weekly":
            frame = frame[frame["period"] == "weekly"]
            self._fair_by_date[date] = frame
        return frame


tag = f"oos_{universe}" + (sys.argv[4] if len(sys.argv) > 4 else "")
et = EdgeTaker()
et.name = f"edge_taker_{tag}"
res, m = run_all.run_one(et, dates, lambda: WeeklyLoader(dates))
print("edge_taker", m["positions_opened"], m["filled_usd"], m.get("pnl_usd"), flush=True)
per_cycle = max(1, round(m["orders"] / max(m["cycles"], 1)))
ctl = sim.RandomControl(orders_per_cycle=per_cycle)
ctl.name = f"random_control_{tag}"
res2, m2 = run_all.run_one(ctl, dates, lambda: WeeklyLoader(dates))
print("control", per_cycle, m2["positions_opened"], m2["filled_usd"], m2.get("pnl_usd"), flush=True)
