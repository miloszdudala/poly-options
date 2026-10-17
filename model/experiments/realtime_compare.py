"""Replay the models on the two re-priced stores (delayed vs real-time chain)."""
import sys
from pathlib import Path

REPO = Path("/Users/miloszdudala/Code/poly-options")
STORE = Path(sys.argv[1])
sys.path.insert(0, str(REPO / "model"))
sys.path.append(str(REPO / "data" / "src"))
import logging
logging.disable(logging.WARNING)

import numpy as np
import pandas as pd

import data
import sim
from models import DirectionBalanced, EdgeTaker, ScenarioHedged

import time as _time
import duckdb as _duckdb
_q = data._q
def _q_retry(sql, _tries=6):
    """A glob that lands on a Parquet file the collector is still writing."""
    for i in range(_tries):
        try:
            return _q(sql)
        except _duckdb.InvalidInputException:
            if i == _tries - 1:
                raise
            _time.sleep(10)
data._q = _q_retry

CUTOFF = pd.Timestamp("2026-10-09 21:00", tz="UTC")


def score(result):
    s = sim.settlements_frame(result)
    s["deadline"] = [pd.Timestamp(x.position.deadline) for x in result.settlements]
    s = s[pd.to_datetime(s["deadline"], utc=True) <= CUTOFF]
    yes = s["outcome_index"] == sim.YES
    out = {"positions": len(s), "cost": s["cost_usd"].sum(), "PnL": s["pnl_usd"].sum(),
           "conf cost": s["cost_confirmed_usd"].sum(), "conf PnL": s["pnl_confirmed_usd"].sum(),
           "YES PnL": s.loc[yes, "pnl_usd"].sum(), "NO PnL": s.loc[~yes, "pnl_usd"].sum()}
    out["ROI"] = out["PnL"] / out["cost"] if out["cost"] else np.nan
    return out


rows = []
for mode in ("base", "rt"):
    data.FAIR_STORE = STORE / mode / "fair"
    dates = [d for d in data.fair_dates() if d <= "2026-10-09"]
    for model in (EdgeTaker(), DirectionBalanced(), ScenarioHedged()):
        r = sim.run(model, dates=dates, params=model.params)
        rows.append({"model": model.name, "options": "delayed" if mode == "base" else "real-time",
                     **score(r)})
        print(rows[-1], flush=True)

pd.set_option("display.width", 220)
print(pd.DataFrame(rows).round(3).to_string(index=False))
