"""Which bets to take, and how many shares of each. Pure functions, no I/O.

This is a **copy** of `sim.candidate_edges` and `sim.walk_ladder`, not an
import of them. The point of the copy is that live rules can change without
moving a replay, and the reverse. The cost is that they can drift apart: when
one changes, decide on purpose whether the other should, and say so in the
commit. As copied on 2026-10-17 the two are the same rule apart from:

* caps are flat per market (`cap_per_market_usd`), not $150 YES / $500 NO;
* no re-entry: a market held on either side is never bought again;
* the walk also enforces the venue's minimum order (5 shares here), which
  the replay does not need because $150 caps never come near it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from settings import Settings

YES, NO = 0, 1


def fee_per_share(price: float, s: Settings) -> float:
    """What a share costs in fees, under the formula the bar is judged on."""
    if s.fee_model == "venue":
        return s.fee_rate * price * (1.0 - price)
    return s.fee_rate * min(price, 1.0 - price)


def all_in_cost(price: float, s: Settings) -> float:
    return price + fee_per_share(price, s)


def candidates(fair: pd.DataFrame, best_asks: pd.DataFrame, s: Settings) -> pd.DataFrame:
    """Every token whose top ask clears the bar, best ROI first.

    `fair` is the engine's frame for one cycle; `best_asks` has one row per
    (market_id, outcome_index) with `ask`, `ticker`, `contract_type`. The
    filters are `sim.candidate_edges`'s, in its order; the comments there say
    why each exists.
    """
    if fair.empty or best_asks.empty:
        return pd.DataFrame()
    f = fair.copy()
    for flag in ("touch_pending", "deep_otm", "no_chain"):
        if flag in f:
            f = f[~f[flag].fillna(False).astype(bool)]
    if "quote_age_s" in f:
        f = f[~(f["quote_age_s"] < 0)]
    if "earnings_gap" in f and "vol_source" in f:
        f = f[~(f["earnings_gap"].fillna(False).astype(bool)
                & (f["vol_source"] != "pre_earnings_flat"))]
    keep = ["market_id", "ticker", "contract_type", "barrier", "fair_K", "fair_atm",
            "deadline_utc", "period", "spot"]
    f = f[[c for c in keep if c in f.columns]]
    merged = best_asks.merge(f, on=["market_id", "ticker", "contract_type"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    ask = merged["ask"].astype(float)
    merged["cost"] = [all_in_cost(p, s) for p in ask]
    is_yes = merged["outcome_index"] == YES
    k, atm = merged["fair_K"], merged["fair_atm"]
    both = k.notna() & atm.notna()
    # The cautious q differs by side: min for a YES, max for a NO.
    q = np.where(both, np.where(is_yes, np.fmin(k, atm), np.fmax(k, atm)), np.nan)
    merged["fair"] = q
    merged["win_prob"] = np.where(is_yes, q, 1.0 - q)
    value = merged["win_prob"] - np.where(is_yes, s.yes_premium, 0.0)
    merged["ev"] = value - merged["cost"]
    merged["roi"] = merged["ev"] / merged["cost"].replace(0.0, np.nan)

    dear = ask > s.high_price
    ev_bar = np.where(dear, s.high_price_min_ev, s.min_ev)
    roi_bar = np.where(dear, s.high_price_min_roi, s.min_roi)
    # The walk's stop: the worse of the two bars, as an all-in cost per share.
    merged["limit_cost"] = np.fmin(value - ev_bar, value / (1.0 + roi_bar))

    ok = (merged["win_prob"].notna() & (merged["ev"] >= ev_bar) & (merged["roi"] >= roi_bar)
          & (merged["cost"] < 1.0) & (ask > 0))
    return merged[ok].sort_values("roi", ascending=False).reset_index(drop=True)


def walk(asks: list[tuple[float, float]], limit_cost: float, budget_usd: float,
         s: Settings, min_shares: float = 0.0) -> list[dict]:
    """Take ask levels, best first, until the limit, the take fraction or the
    money stops it. Returns [] if the result is under $1 or under the venue's
    minimum order, because neither could be placed."""
    taken: list[dict] = []
    left = budget_usd
    for price, size in sorted(asks):
        if not (0.0 < price < 1.0):
            continue
        cost = all_in_cost(price, s)
        if cost >= limit_cost:
            break
        shares = min(s.take_fraction * size, left / cost)
        if shares <= 0:
            break
        taken.append({"price": price, "shares": shares, "cost_usd": shares * cost})
        left -= shares * cost
        if left <= 1e-9:
            break
    total_shares = sum(t["shares"] for t in taken)
    if sum(t["cost_usd"] for t in taken) < 1.0 or total_shares < min_shares:
        return []
    return taken
