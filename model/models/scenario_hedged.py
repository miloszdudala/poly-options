"""`scenario_hedged`: no scenario may lose more than `loss_budget`.

The user's idea (2026-10-09): YES and NO should offset "the market moves a
lot" against "the market stands still", and hit_up against hit_down should
offset "up" against "down". balanced_book tried that with equal dollars per
quadrant, which is not equal risk: $500 of NO at 93c earns $37 in a calm
week, $500 of YES at 30c loses $500. This model balances what each position
*pays* in each scenario instead.

Scenarios: every stock moves the same number of its own sigmas, `z`, at some
point before its deadline - z in {-3 .. +3}, and z = 0 for "nothing moves".
A barrier at distance `ln(B/S) / (sigma_atm * sqrt(T))` is touched in the
scenario if the move reaches it. All tickers move together, the case a hedge
is for. A path that touches both sides is not modelled.

An order goes in at the largest size that keeps every scenario's PnL for the
whole book above `-loss_budget`. Same signal as edge_taker.
"""
from __future__ import annotations

import math

import numpy as np

import sim
from sim import Order, Params, YES

SCENARIOS = np.array([-3.0, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0])


def payoff(contract_type: str, outcome_index: int, z_barrier: float) -> np.ndarray:
    """1 or 0 per share in each scenario."""
    if contract_type == "hit_up":
        touched = SCENARIOS >= z_barrier
    else:
        touched = SCENARIOS <= z_barrier
    return touched.astype(float) if outcome_index == YES else (~touched).astype(float)


def barrier_z(row) -> float:
    """Distance to the barrier in the stock's own sigmas over the remaining horizon."""
    try:
        spot, barrier = float(row["spot"]), float(row["barrier"])
        sigma, years = float(row["sigma_atm"]), float(row["T_years"])
    except (KeyError, TypeError, ValueError):
        return float("nan")
    if not (spot > 0 and barrier > 0 and sigma > 0 and years > 0):
        return float("nan")
    return math.log(barrier / spot) / (sigma * math.sqrt(years))


class ScenarioHedged:
    name = "scenario_hedged"

    #: Ticker cap 12%: the scenario budget is the binding constraint, the
    #: direction cap is replaced by it.
    params = Params(ticker_cap_frac=0.12, direction_cap_frac=1.0)

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08,
                 loss_budget: float = 1_000.0):
        self.min_ev = min_ev
        self.min_roi = min_roi
        self.loss_budget = loss_budget
        self.refusals: dict[str, int] = {}
        self._last_z: dict[str, float] = {}       # a held market not priced this cycle

    def _z(self, state, market_id: str) -> tuple[float, bool]:
        row = sim._row_for(state.fair, market_id)
        if row is None:
            return self._last_z.get(market_id, float("nan")), False
        if bool(row.get("touch_pending", False)):
            return float("nan"), True
        z = barrier_z(row)
        if np.isfinite(z):
            self._last_z[market_id] = z
        return z, False

    def book_pnl(self, state) -> np.ndarray:
        """Each scenario's PnL for everything held, if it settled that way."""
        total = np.zeros(len(SCENARIOS))
        for p in state.portfolio.open.values():
            z, touched = self._z(state, p.market_id)
            if touched:      # already decided: YES is paid, NO is lost
                pays = np.ones(len(SCENARIOS)) * (1.0 if p.outcome_index == YES else 0.0)
            elif np.isfinite(z):
                pays = payoff(p.contract_type, p.outcome_index, z)
            else:            # nothing to place it with: assume the worst
                pays = np.zeros(len(SCENARIOS))
            total += p.shares * pays - p.cost_usd
        return total

    def propose(self, state: sim.CycleState) -> list[Order]:
        candidates = sim.candidate_edges(state, min_ev=self.min_ev, min_roi=self.min_roi)
        if candidates.empty:
            return []
        params = state.params
        book = self.book_pnl(state)
        orders: list[Order] = []
        self.refusals = {}

        for row in candidates.itertuples():
            outcome = int(row.outcome_index)
            z, _ = self._z(state, row.market_id)
            if not np.isfinite(z):
                self._note("no_distance")
                continue
            cap = params.cap_yes_usd if outcome == YES else params.cap_no_usd
            order = Order(market_id=row.market_id, outcome_index=outcome, max_usd=cap,
                          limit_price=float(row.limit_price), reason="scenario_hedged",
                          fair=float(row.fair), fair_source=str(row.fair_source))
            proj = sim.project(state, order)
            if proj["cost_usd"] < params.min_fill_usd:
                self._note("no_depth")
                continue
            pays = payoff(row.contract_type, outcome, z)
            leg = proj["shares"] * pays - proj["cost_usd"]
            scale = self._largest_scale(book, leg)
            if scale < 1.0:
                if scale * proj["cost_usd"] < params.min_fill_usd:
                    self._note("scenario_loss")
                    continue
                order = Order(**{**order.__dict__, "max_usd": proj["cost_usd"] * scale})
                proj = sim.project(state, order)
                leg = proj["shares"] * pays - proj["cost_usd"]
                if proj["cost_usd"] < params.min_fill_usd or (book + leg).min() < -self.loss_budget - 1e-6:
                    self._note("scenario_loss")
                    continue
            book = book + leg
            orders.append(order)
        return orders

    def _largest_scale(self, book: np.ndarray, leg: np.ndarray) -> float:
        """Largest t in [0, 1] with every scenario of `book + t * leg` above the budget."""
        t = 1.0
        for b, l in zip(book, leg):
            if l < 0:
                t = min(t, (b + self.loss_budget) / -l)
        return max(t, 0.0)

    def _note(self, key: str) -> None:
        self.refusals[key] = self.refusals.get(key, 0) + 1
