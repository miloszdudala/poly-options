"""`direction_balanced`: hold as much "stock goes up" as "stock goes down".

The user's proposal (2026-10-09), replacing balanced_book's four quadrants
with the two directions they collapse into:

| position        | says the stock will |
|-----------------|---------------------|
| YES on hit_up   | go up               |
| NO on hit_down  | go up (not fall)    |
| YES on hit_down | go down             |
| NO on hit_up    | go down (not rise)  |

Measured in max loss (dollars paid), a direction may exceed the other by at
most `balance_slack`. Same signal as edge_taker, no deltas, no betas.

What it does not balance is volatility: NO on both sides is a short
straddle, flat in direction and still losing on a large move either way.
"""
from __future__ import annotations

import sim
from sim import LONG_STOCK, SHORT_STOCK, Order, Params, YES


class DirectionBalanced:
    name = "direction_balanced"

    #: Ticker cap a little looser than balanced_book's 8%, since two buckets
    #: bind less than four; the direction rule replaces the direction cap.
    params = Params(ticker_cap_frac=0.10, direction_cap_frac=1.0)

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08,
                 balance_slack: float = 500.0):
        self.min_ev = min_ev
        self.min_roi = min_roi
        self.balance_slack = balance_slack
        self.refusals: dict[str, int] = {}

    def propose(self, state: sim.CycleState) -> list[Order]:
        candidates = sim.candidate_edges(state, min_ev=self.min_ev, min_roi=self.min_roi)
        if candidates.empty:
            return []
        params = state.params
        held = {d: state.portfolio.direction_usd(d) for d in (LONG_STOCK, SHORT_STOCK)}
        sides = dict(held)
        orders: list[Order] = []
        self.refusals = {}

        for row in candidates.itertuples():
            outcome = int(row.outcome_index)
            side = sim.direction_of(row.contract_type, outcome)
            other = SHORT_STOCK if side == LONG_STOCK else LONG_STOCK
            # The other side counts at what the book holds, not at this cycle's
            # projections, for balanced_book's reason: a projection the harness
            # then refuses must not license an order on the opposite side.
            allowance = held[other] + self.balance_slack - sides[side]
            if allowance < params.min_fill_usd:
                self.refusals["direction_balance"] = self.refusals.get("direction_balance", 0) + 1
                continue
            cap = params.cap_yes_usd if outcome == YES else params.cap_no_usd
            order = Order(market_id=row.market_id, outcome_index=outcome,
                          max_usd=min(cap, allowance), limit_price=float(row.limit_price),
                          reason="direction_balanced", fair=float(row.fair),
                          fair_source=str(row.fair_source))
            projection = sim.project(state, order)
            if projection["cost_usd"] < params.min_fill_usd:
                self.refusals["no_depth"] = self.refusals.get("no_depth", 0) + 1
                continue
            sides[side] += projection["cost_usd"]
            orders.append(order)
        return orders
