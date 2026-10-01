"""A — `edge_taker`: take every edge, and let the book fall where it falls.

MODELS 5.1. No portfolio constraint at all, which is the point: this model
measures whether the mispricing exists and survives costs, not whether a
tradeable portfolio can be built out of it. Read its concentration and its
beta-dollar-delta as the measurement they are, not as a recommendation.

The per-market cap stays, because "size to the per-market cap" is how 5.1
sizes; the per-ticker and per-direction caps are lifted to 100%, so the only
portfolio-level thing that can stop this model is running out of cash.
"""
from __future__ import annotations

import sim
from sim import Order, Params


class EdgeTaker:
    name = "edge_taker"

    #: MODELS 4.4's caps with the portfolio ones removed (5.1: "no portfolio
    #: constraint at all"). Cash still binds: bankroll is not leverage.
    params = Params(ticker_cap_frac=1.0, direction_cap_frac=1.0)

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08):
        self.min_ev = min_ev
        self.min_roi = min_roi

    def propose(self, state: sim.CycleState) -> list[Order]:
        candidates = sim.candidate_edges(state, min_ev=self.min_ev, min_roi=self.min_roi)
        if candidates.empty:
            return []
        cap = state.params
        # First come first served by ROI (MODELS 5.1). `candidate_edges` already
        # sorts that way and `execute` fills in the order given, so the cash
        # that runs out runs out on the worst edge of the cycle, not a random one.
        return [Order(market_id=row.market_id, outcome_index=int(row.outcome_index),
                      max_usd=cap.cap_yes_usd if int(row.outcome_index) == sim.YES
                      else cap.cap_no_usd,
                      limit_price=float(row.limit_price), reason="edge",
                      fair=float(row.fair), fair_source=str(row.fair_source))
                for row in candidates.itertuples()]
