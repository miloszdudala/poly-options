"""B-simple — `balanced_book`: B's discipline without B's Greeks.

MODELS 5.2b. The user's proposal was to hold equal YES and NO so the
correlated risk cancels; the idea is right and the axis is wrong, because
YES/NO is not the risk axis of a hit market:

| position        | stock exposure | vol exposure |
|-----------------|----------------|--------------|
| YES on hit_up   | long           | long         |
| YES on hit_down | short          | long         |
| NO on hit_down  | long           | short        |
| NO on hit_up    | short          | long->short  |

A book that is exactly half YES and half NO can be entirely long the market
(YES `hit_up` plus NO `hit_down`). So this model balances the four quadrants
instead, measured in **max loss** - the dollars paid, which for a bought
position is all that can be lost.

No deltas, no betas, one parameter. It exists to be reported next to
`factor_neutral`, so that the value of the Greeks is measured rather than
assumed: if the two books earn the same and carry the same drawdown, the
Greeks are ceremony.
"""
from __future__ import annotations

import sim
from sim import Order, Params, YES


class BalancedBook:
    name = "balanced_book"

    #: Per-ticker cap as in B (MODELS 5.2b). The per-direction cap is left at
    #: 100% because the quadrant rule is a strictly stronger statement about
    #: the same axis - two quadrants make up a direction - and running both
    #: would leave the report unable to say which one bound.
    params = Params(ticker_cap_frac=0.08, direction_cap_frac=1.0)

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
        held = state.portfolio.quadrant_totals()      # what the book holds now
        quadrants = dict(held)                         # plus this cycle's projections
        orders: list[Order] = []
        self.refusals = {}

        for row in candidates.itertuples():
            outcome = int(row.outcome_index)
            quadrant = sim.quadrant_of(row.contract_type, outcome)
            # The allowance is measured against the *smallest* quadrant, so an
            # empty opposite quadrant caps this one at the slack and no more.
            # The floor for *other* quadrants is what the portfolio holds, not
            # what this cycle's projections promise: the first version let a
            # projected YES that the harness then refused (already held,
            # MODELS 4.2) license a NO in another quadrant, and the slack was
            # breached by exactly that amount (behaviour checks, 2026-09-18).
            # A quadrant's own projected fills do raise its floor, because if
            # they fail it is only smaller than assumed, never larger.
            floor = min(min(v for k, v in held.items() if k != quadrant), quadrants[quadrant])
            allowance = floor + self.balance_slack - quadrants[quadrant]
            if allowance < params.min_fill_usd:
                self.refusals["quadrant_balance"] = self.refusals.get("quadrant_balance", 0) + 1
                continue
            cap = params.cap_yes_usd if outcome == YES else params.cap_no_usd
            order = Order(market_id=row.market_id, outcome_index=outcome,
                          max_usd=min(cap, allowance), limit_price=float(row.limit_price),
                          reason="balanced", fair=float(row.fair),
                          fair_source=str(row.fair_source))
            projection = sim.project(state, order)
            if projection["cost_usd"] < params.min_fill_usd:
                self.refusals["no_depth"] = self.refusals.get("no_depth", 0) + 1
                continue
            quadrants[quadrant] += projection["cost_usd"]
            orders.append(order)
        return orders
