"""$50 books that spend on the best edges, not on whatever comes first.

Variants (both sides always; same signal as edge_taker, sim.candidate_edges):
  all-days   - any market, any day: the book fills on the first days, then waits
  wk Mon-Wed - weekly markets only, buying Monday to Wednesday (ET); the cash
               comes back on Friday's resolution (or the touch) for next week
  wk Mon-Tue - the same, Monday and Tuesday only
each at EV bars 3/6/10/15c. $5 a market, at least 5 shares (Polymarket's
minimum), half a level at most. Fees as in the replays (slightly high).

Only markets whose deadline passed by 2026-10-09 are scored: the 10-16
weeklies resolve tonight and later monthlies have settled only if touched.
"""
import sys
from pathlib import Path

REPO = Path("/Users/miloszdudala/Code/poly-options")
sys.path.insert(0, str(REPO / "model"))
sys.path.append(str(REPO / "data" / "src"))
import logging
logging.disable(logging.WARNING)

import pandas as pd

import data
import sim
from sim import Order, Params

# The collector writes a Parquet file every 15 minutes; a glob that lands
# mid-write sees a file with no footer yet. Wait and read again.
import time as _time
import duckdb as _duckdb
_q = data._q
def _q_retry(sql, _tries=6):
    for i in range(_tries):
        try:
            return _q(sql)
        except _duckdb.InvalidInputException:
            if i == _tries - 1:
                raise
            _time.sleep(10)
data._q = _q_retry

ET = "America/New_York"
CUTOFF = pd.Timestamp("2026-10-09 21:00", tz="UTC")
PARAMS = Params(bankroll=50.0, cap_yes_usd=5.0, cap_no_usd=5.0,
                ticker_cap_frac=1.0, direction_cap_frac=1.0)
MIN_SHARES = 5.0


class SmallBook:
    def __init__(self, name, min_ev, weekly_only=False, days=None):
        self.name, self.min_ev, self.weekly_only, self.days = name, min_ev, weekly_only, days
        self.params = PARAMS

    def propose(self, state):
        if self.days is not None and state.now.tz_convert(ET).weekday() not in self.days:
            return []
        c = sim.candidate_edges(state, min_ev=self.min_ev, min_roi=0.08)
        if c.empty:
            return []
        if self.weekly_only:
            c = c[c["period"] == "weekly"]
        orders = []
        for row in c.itertuples():
            order = Order(market_id=row.market_id, outcome_index=int(row.outcome_index),
                          max_usd=5.0, limit_price=float(row.limit_price), reason="small",
                          fair=float(row.fair), fair_source=str(row.fair_source))
            if sim.project(state, order)["shares"] >= MIN_SHARES:
                orders.append(order)
        return orders


def score(result):
    s = sim.settlements_frame(result)
    if s.empty:
        return {}
    s["deadline"] = [pd.Timestamp(x.position.deadline) for x in result.settlements]
    s = s[pd.to_datetime(s["deadline"], utc=True) <= CUTOFF]
    yes = s["outcome_index"] == sim.YES
    weeks = 4  # 09-14 .. 10-09
    return {"bets": len(s), "bets/wk": round(len(s) / weeks, 1),
            "YES": int(yes.sum()), "NO": int((~yes).sum()),
            "cost": round(s["cost_usd"].sum(), 2), "PnL": round(s["pnl_usd"].sum(), 2),
            "ROI": f"{s['pnl_usd'].sum() / s['cost_usd'].sum():+.1%}",
            "conf cost": round(s["cost_confirmed_usd"].sum(), 2),
            "conf PnL": round(s["pnl_confirmed_usd"].sum(), 2),
            "losers": int((s["pnl_usd"] < 0).sum()),
            "worst": round(s["pnl_usd"].min(), 2)}


if __name__ == "__main__":
    dates = [d for d in data.fair_dates() if d <= "2026-10-09"]
    rows = []
    for label, kw in [("all-days", {}), ("wk Mon-Wed", {"weekly_only": True, "days": {0, 1, 2}}),
                      ("wk Mon-Tue", {"weekly_only": True, "days": {0, 1}})]:
        for ev in (0.03, 0.06, 0.10, 0.15):
            model = SmallBook(f"{label} ev{int(ev*100)}", ev, **kw)
            r = sim.run(model, dates=dates, params=PARAMS)
            rows.append({"variant": label, "min_ev": ev, **score(r)})
            print(rows[-1], flush=True)
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print(out.to_string(index=False))
