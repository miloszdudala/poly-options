"""Step 9 - fills: what an order gets from the book.

Walks the token's own asks by `depth_rank`, best first, with three stops at
every level: the limit (an all-in cost at or above it ends the walk), at most
`take_fraction` (0.5, a guess: the rest may be gone when the order lands) of
the level's size, and the money left. A walk under `min_fill_usd` ($1,
Polymarket's minimum order) is no fill. A fill counts if the book had the
size when we looked (CLAUDE.md, Decided).
"""
from __future__ import annotations

import pandas as pd


def walk_ladder(ladder: pd.DataFrame, limit: float, budget_usd: float, fee,
                take_fraction: float = 0.5, min_fill_usd: float = 1.0) -> list[dict]:
    taken: list[dict] = []
    left = budget_usd
    for level in ladder.sort_values("depth_rank").itertuples():
        price = float(level.price)
        if not (0.0 < price < 1.0):
            continue
        cost_per_share = price + fee(price)
        if cost_per_share >= limit:
            break
        shares = min(take_fraction * float(level.size), left / cost_per_share)
        if shares <= 0:
            break
        spend = shares * cost_per_share
        taken.append({"depth_rank": int(level.depth_rank), "price": price, "shares": shares,
                      "fee_usd": fee(price) * shares, "cost_usd": spend,
                      "level_size": float(level.size)})
        left -= spend
        if left <= 1e-9:
            break
    if sum(t["cost_usd"] for t in taken) < min_fill_usd:
        return []
    return taken
