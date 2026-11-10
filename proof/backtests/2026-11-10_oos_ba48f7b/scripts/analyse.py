"""Weekly-hit, settled-only summary of the oos ledgers written by oos_run.py."""
import sys
from pathlib import Path

import pandas as pd

led = Path(sys.argv[1]) / "model" / "store" / "ledger"


def load(model):
    f = pd.read_parquet(led / model / "fills")
    r = pd.read_parquet(led / model / "resolutions")
    return f, r


def summarise(model, by_week=False):
    f, r = load(model)
    f = f[(f["period"] == "weekly") & f["contract_type"].isin(["hit_up", "hit_down"])].copy()
    wp = f["fair"].where(f["outcome_index"] == 0, 1 - f["fair"])
    f["ev"] = f["shares"] * wp - f["cost_usd"]
    ev = f.groupby(["market_id", "outcome_index"])["ev"].sum().rename("ev_usd")
    meta = f.groupby(["market_id", "outcome_index"])["deadline_utc"].first()
    open_n = f.groupby(["market_id", "outcome_index"]).ngroups
    r = r.set_index(["market_id", "outcome_index"])
    s = r.join(ev, how="inner").join(meta)
    s["week"] = pd.to_datetime(s["deadline_utc"], utc=True).dt.strftime("%Y-%m-%d")

    def row(g):
        return pd.Series({"bets": len(g), "staked": g["cost_usd"].sum(),
                          "ev": g["ev_usd"].sum(), "pnl": g["pnl_usd"].sum(),
                          "roi": g["pnl_usd"].sum() / g["cost_usd"].sum(),
                          "yes": int((g.index.get_level_values(1) == 0).sum()),
                          "won": int((g["payout_per_share"] >= 1).sum())})

    total = row(s)
    print(f"\n## {model}: weekly hit positions {open_n}, settled {len(s)}")
    print(total.round(2).to_string())
    if by_week:
        wk = s.groupby("week").apply(row)
        print(wk.round(3).to_string())
    return s


wt = sys.argv[1]
for model in ["edge_taker_oos_weekly", "random_control_oos_weekly",
              "edge_taker_cmp_base", "edge_taker_cmp_rt"]:
    summarise(model, by_week=True)
