"""Every (market, side, cycle) the option engine priced, with its outcome.

    .venv/bin/python model/opportunities.py            # writes model/store/opportunities.parquet

The harness in `sim.py` answers "what would this model have done", which is
path-dependent: cash runs out, a market is entered once, the order of the
cycle decides who gets the depth. That is the right question for a model and
the wrong one for "what if the edge bar were X", because changing X changes
the path and the comparison mixes the two effects.

This panel removes the path. One row per market side per priced session
cycle: the top of the ask ladder, the size there, whether that price level
was still showing at the next cycle (MODELS 4.2's persistence check), the
engine's fairs, its flags, and the outcome. A threshold sweep is then a
group-by: take the first cycle each market side clears the bar, buy the top
level, settle it. Nothing here is seen by a model; it is an analysis table.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))

import data  # noqa: E402
import sim  # noqa: E402

OUT = HERE / "store" / "opportunities.parquet"
FEE = 0.04

FAIR_COLS = ["market_id", "ticker", "contract_type", "barrier", "period", "deadline_utc",
             "fair_K", "fair_atm", "fair_cont", "fair_empirical", "delta", "spot",
             "sigma_K", "sigma_atm", "sigma_realised", "T_sessions", "deep_otm", "no_chain",
             "touch_pending", "stale_book", "earnings_gap", "earnings_inside", "vol_source",
             "quote_age_s", "book_age_s", "clamped_strike"]


def _top(books: pd.DataFrame) -> pd.DataFrame:
    asks = books[(books["side"] == "ask") & (books["depth_rank"] == 0)]
    bids = books[(books["side"] == "bid") & (books["depth_rank"] == 0)]
    top = asks[["market_id", "outcome_index", "price", "size"]].rename(
        columns={"price": "ask", "size": "ask_size"})
    return top.merge(bids[["market_id", "outcome_index", "price"]].rename(columns={"price": "bid"}),
                     on=["market_id", "outcome_index"], how="left")


def _size_at(books: pd.DataFrame) -> pd.DataFrame:
    """Size showing at each (market, side, ask price), for the persistence check."""
    asks = books[books["side"] == "ask"]
    return (asks.groupby(["market_id", "outcome_index", "price"], as_index=False)["size"].sum()
            .rename(columns={"price": "ask", "size": "next_size_at_ask"}))


def build(dates: list[str] | None = None) -> pd.DataFrame:
    dates = dates or data.fair_dates()
    loader = sim.Loader(dates)
    cycles = loader.cycles()
    frames = []
    nxt_books = None
    for i, cycle in enumerate(cycles):
        books = loader.books(cycle)
        fair = loader.fair(cycle)
        if books.empty or fair.empty:
            continue
        top = _top(books)
        if i + 1 < len(cycles):
            nxt_books = loader.books(cycles[i + 1])
            top = top.merge(_size_at(nxt_books), on=["market_id", "outcome_index", "ask"], how="left")
            top["next_size_at_ask"] = top["next_size_at_ask"].fillna(0.0)
        else:
            top["next_size_at_ask"] = np.nan
        f = fair[[c for c in FAIR_COLS if c in fair.columns]]
        panel = top.merge(f, on="market_id", how="inner")
        panel.insert(0, "cycle_id", cycle)
        frames.append(panel)
    out = pd.concat(frames, ignore_index=True)

    res = data.resolutions_upto(pd.Timestamp.now(tz="UTC"))
    res = res[["market_id", "result", "settled_at_utc", "outcome_prices"]]
    out = out.merge(res, on="market_id", how="left")
    yes = out["outcome_index"] == sim.YES
    payout = np.where(out["result"] == "Yes", 1.0, np.where(out["result"] == "No", 0.0, np.nan))
    fifty = out["result"].isna() & out["outcome_prices"].astype(str).str.contains("0.5")
    payout = np.where(fifty, 0.5, payout)
    out["payout"] = np.where(yes, payout, 1.0 - payout)

    out["cost"] = out["ask"] + FEE * np.minimum(out["ask"], 1.0 - out["ask"])
    k, atm = out["fair_K"], out["fair_atm"]
    both = k.notna() & atm.notna()
    q = np.where(both, np.where(yes, np.fmin(k, atm), np.fmax(k, atm)), np.nan)
    out["win_prob"] = np.where(yes, q, 1.0 - q)
    # YES is charged the risk premium, as in `sim.candidate_edges`.
    out["ev"] = out["win_prob"] - np.where(yes, sim.YES_RISK_PREMIUM, 0.0) - out["cost"]
    out["roi"] = out["ev"] / out["cost"]
    emp = out["fair_empirical"]
    out["win_prob_emp"] = np.where(yes, emp, 1.0 - emp)
    out["yes_mid"] = np.nan   # filled below from the YES token's own book
    ymid = out[yes].assign(m=lambda d: (d["ask"] + d["bid"]) / 2)[["cycle_id", "market_id", "m"]]
    out = out.drop(columns="yes_mid").merge(ymid.rename(columns={"m": "yes_mid"}),
                                            on=["cycle_id", "market_id"], how="left")
    return out


if __name__ == "__main__":
    panel = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT, index=False)
    print(f"{len(panel):,} rows, {panel['cycle_id'].nunique()} cycles, "
          f"{panel['market_id'].nunique():,} markets -> {OUT}")
