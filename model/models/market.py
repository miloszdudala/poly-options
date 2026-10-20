"""The harness side of a model: books and fair values in, orders out.

Everything a model needs to know about Polymarket and the replay lives here,
so the model files can be read as betting rules alone (`models/bets.py`):

* `offers(state)` - every bet on offer this cycle that has non-negative EV,
  best ROI first, as `Bet`s. The signal itself (which fair, the YES risk
  premium, the data-quality filters) is `sim.candidate_edges`; a model then
  applies its own bar with `Bet.clears`.
* `holdings(state, memory)` - the open positions, as `Holding`s.
* `run_cycle(model, state)` - the two above, `model.decide`, and the model's
  takes turned into the harness's `Order`s.
"""
from __future__ import annotations

import numpy as np

import sim
from models.bets import NO, YES, Bet, Holding, Quote

_SIDE = {sim.YES: YES, sim.NO: NO}
_INDEX = {YES: sim.YES, NO: sim.NO}


def offers(state: sim.CycleState) -> list[Bet]:
    """Every bet with EV >= 0 and ROI >= 0 (>= 2% above 90c), best ROI first."""
    rows = sim.candidate_edges(state, min_ev=0.0, min_roi=0.0)
    out = []
    for row in rows.itertuples():
        out.append(Bet(
            market_id=row.market_id, ticker=row.ticker, kind=row.contract_type,
            side=_SIDE[int(row.outcome_index)], ask=float(row.ask), cost=float(row.cost),
            win_prob=float(row.win_prob), value=float(row.win_prob) - float(row.premium),
            touch_prob=float(row.fair), fair_source=str(row.fair_source),
            _quote=_quoter(state, row.market_id, int(row.outcome_index), float(row.fair),
                           str(row.fair_source))))
    return out


def _quoter(state, market_id: str, outcome_index: int, fair: float, fair_source: str):
    def quote(usd: float, limit: float) -> Quote:
        order = sim.Order(market_id=market_id, outcome_index=outcome_index, max_usd=usd,
                          limit_price=limit, reason="quote", fair=fair, fair_source=fair_source)
        p = sim.project(state, order)
        return Quote(shares=p["shares"], cost=p["cost_usd"])
    return quote


def holdings(state: sim.CycleState, memory: dict) -> list[Holding]:
    """The open positions, with the model's touch probability for each now.

    The probability is the conservative engine number for the side held (the
    smaller of fair_K / fair_atm for a YES, the larger for a NO, as the signal
    uses). A market not priced this cycle keeps the last one seen, else the
    one it was bought at. A market whose touch is pending has none.
    """
    out = []
    for p in state.portfolio.open.values():
        row = sim._row_for(state.fair, p.market_id)
        key = (p.market_id, p.outcome_index)
        q = memory.get(key, p.entry_fair)
        if row is not None and bool(row.get("touch_pending", False)):
            q = None
        elif row is not None:
            k, atm = float(row.get("fair_K", np.nan)), float(row.get("fair_atm", np.nan))
            if np.isfinite(k) and np.isfinite(atm):
                q = min(k, atm) if p.outcome_index == sim.YES else max(k, atm)
                memory[key] = q
        out.append(Holding(market_id=p.market_id, ticker=p.ticker, kind=p.contract_type,
                           side=_SIDE[p.outcome_index], shares=p.shares, cost=p.cost_usd,
                           touch_prob=q))
    return out


def run_cycle(model, state: sim.CycleState) -> list[sim.Order]:
    model._now = state.now
    model._this_cycle = []
    model.refusals = {}
    model.holdings = holdings(state, model._memory)
    model.decide(offers(state))
    return [sim.Order(market_id=t.bet.market_id, outcome_index=_INDEX[t.bet.side],
                      max_usd=t.usd, limit_price=t.limit, reason=t.reason,
                      fair=t.bet.touch_prob, fair_source=t.bet.fair_source)
            for t in model._this_cycle]
