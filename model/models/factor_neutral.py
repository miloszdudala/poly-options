"""B — `factor_neutral`: A's signal, sized so a 4% market day is survivable.

MODELS 5.2. Four constraints on top of the signal, and they are not
interchangeable:

* the **beta-dollar-delta band**, +/-5% of bankroll at every cycle, which is
  what stops a book of longshots from being a leveraged bet on the index;
* **per-direction 25% and per-ticker 8%**, tighter than MODELS 4.4's defaults
  and enforced by the harness through `params`;
* the **pair preference**: a `hit_up` YES and a `hit_down` YES on the same
  ticker that both clear the bar are taken together, as a synthetic straddle
  whose delta is near zero and which pays on any large move;
* a **variance budget** of `(2% of bankroll)^2` per day, with positions on one
  ticker fully correlated and positions in one direction across tickers at
  0.5.

The band is checked against a *projected* fill, not against the order, because
a digital's dollar-delta has almost nothing to do with its price: a 30c YES on
a near barrier carries several hundred dollars of stock exposure per $10 of
premium. Sizing on premium and checking on premium would leave the band
untouched and the exposure enormous.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import sim
from sim import Order, Params, YES


class FactorNeutral:
    name = "factor_neutral"

    #: MODELS 5.2's own caps, not MODELS 4.4's.
    params = Params(ticker_cap_frac=0.08, direction_cap_frac=0.25)

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08,
                 beta_band_frac: float = 0.05, variance_budget_frac: float = 0.02,
                 same_direction_rho: float = 0.5):
        self.min_ev = min_ev
        self.min_roi = min_roi
        self.beta_band_frac = beta_band_frac
        self.variance_budget_frac = variance_budget_frac
        self.same_direction_rho = same_direction_rho
        #: Filled in by `propose` each cycle so the report can say which
        #: constraint did the refusing rather than guessing.
        self.refusals: dict[str, int] = {}

    # ------------------------------------------------------------ helpers

    def _note(self, why: str) -> None:
        self.refusals[why] = self.refusals.get(why, 0) + 1

    def _candidate_order(self, row, params) -> Order:
        outcome = int(row.outcome_index)
        return Order(market_id=row.market_id, outcome_index=outcome,
                     max_usd=params.cap_yes_usd if outcome == YES else params.cap_no_usd,
                     limit_price=float(row.limit_price), reason="edge_neutral",
                     fair=float(row.fair), fair_source=str(row.fair_source))

    @staticmethod
    def _beta_delta(row, shares: float, beta: float) -> float:
        """Signed beta-dollar-delta of a prospective position. MODELS 5.2.

        The engine's `delta` is the YES-side `dP/dS`; a NO is the complement,
        so its delta is the negative. That is why NO on `hit_down` is positive
        delta even though `hit_down` YES is negative.
        """
        delta, spot = float(row.delta), float(row.spot)
        if not (np.isfinite(delta) and np.isfinite(spot)):
            return 0.0
        signed = delta if int(row.outcome_index) == YES else -delta
        return signed * spot * shares * beta

    def _variance(self, positions: list[dict]) -> float:
        """`(2% bankroll)^2` budget, MODELS 5.2's crude-but-honest correlation.

        `q(1-q) * notional^2` with `notional` = dollars paid, which is the max
        loss. Using shares - the true swing of the payoff - would be several
        times larger and would refuse almost every second position; the spec
        says notional and the spec's reading is the loose one.
        """
        n = len(positions)
        if not n:
            return 0.0
        v = np.array([p["q"] * (1.0 - p["q"]) * p["cost"] ** 2 for p in positions])
        total = float(v.sum())
        for i in range(n):
            for j in range(i + 1, n):
                a, b = positions[i], positions[j]
                if a["ticker"] == b["ticker"]:
                    rho = 1.0
                elif a["direction"] == b["direction"]:
                    rho = self.same_direction_rho
                else:
                    rho = 0.0
                if rho:
                    total += 2.0 * rho * math.sqrt(v[i] * v[j])
        return total

    def _held(self, state) -> list[dict]:
        out = []
        for p in state.portfolio.open.values():
            row = sim._row_for(state.fair, p.market_id)
            fair = float(row["fair_K"]) if row is not None else p.entry_fair
            q = p.win_probability(fair)
            out.append({"q": q if np.isfinite(q) else 0.5, "cost": p.cost_usd,
                        "ticker": p.ticker, "direction": p.direction})
        return out

    # -------------------------------------------------------------- pairs

    @staticmethod
    def _pairs(candidates: pd.DataFrame) -> tuple[list[tuple], pd.DataFrame]:
        """Split the candidates into straddle pairs and everything else.

        MODELS 5.2: a `hit_up` YES and a `hit_down` YES on the same ticker that
        both clear the bar are taken together. Pairs go first, ranked by their
        combined ROI, because taking them one at a time would let the beta band
        refuse the first leg and never see the second - which is the one case
        where the band is refusing a position that would have *reduced* risk.
        """
        yes = candidates[candidates["outcome_index"] == YES]
        pairs, used = [], set()
        for ticker, group in yes.groupby("ticker"):
            ups = group[group["contract_type"] == "hit_up"]
            downs = group[group["contract_type"] == "hit_down"]
            if ups.empty or downs.empty:
                continue
            up, down = ups.iloc[0], downs.iloc[0]
            pairs.append((up, down, float(up["roi"] + down["roi"])))
            used.update({(up["market_id"], YES), (down["market_id"], YES)})
        pairs.sort(key=lambda p: -p[2])
        singles = candidates[[(m, o) not in used for m, o in
                              zip(candidates["market_id"], candidates["outcome_index"])]]
        return pairs, singles

    # ------------------------------------------------------------ propose

    def propose(self, state: sim.CycleState) -> list[Order]:
        candidates = sim.candidate_edges(state, min_ev=self.min_ev, min_roi=self.min_roi)
        if candidates.empty:
            return []
        params = state.params
        band = self.beta_band_frac * params.bankroll
        budget = (self.variance_budget_frac * params.bankroll) ** 2

        net = state.portfolio.beta_dollar_delta(state.fair, state.betas)
        book = self._held(state)
        orders: list[Order] = []
        self.refusals = {}

        pairs, singles = self._pairs(candidates)
        for up, down, _ in pairs:
            accepted, net, book = self._try(state, [up, down], net, book, band, budget,
                                            reason="straddle_pair")
            orders += accepted
        for row in singles.itertuples():
            accepted, net, book = self._try(state, [row], net, book, band, budget,
                                            reason="edge_neutral")
            orders += accepted
        return orders

    def _try(self, state, rows, net: float, book: list[dict], band: float,
             budget: float, reason: str):
        """Accept a candidate (or a pair, all or nothing), trimming for the band.

        The trim is proportional: halving the dollars roughly halves the shares
        and therefore the delta, because `walk_ladder` spends its budget from
        the top of the ladder down. It is an approximation and it is checked
        afterwards against the projection, not assumed.
        """
        params = state.params
        rows = [r if not hasattr(r, "_asdict") else r for r in rows]
        orders = [self._candidate_order(r, params) for r in rows]
        projections = [sim.project(state, o) for o in orders]
        if any(p["cost_usd"] < params.min_fill_usd for p in projections):
            self._note("no_depth")
            return [], net, book

        exposure = sum(self._beta_delta(r, p["shares"], float(state.betas.get(r.ticker, 1.0)))
                       for r, p in zip(rows, projections))
        scale = 1.0
        if abs(net + exposure) > band:
            if abs(exposure) < 1e-9:
                self._note("beta_band")
                return [], net, book
            # The largest share of the candidate that still lands inside the band.
            target = math.copysign(band, net + exposure)
            scale = (target - net) / exposure
            if not (0.0 < scale < 1.0):
                self._note("beta_band")
                return [], net, book
            orders = [Order(**{**o.__dict__, "max_usd": o.max_usd * scale}) for o in orders]
            projections = [sim.project(state, o) for o in orders]
            if any(p["cost_usd"] < params.min_fill_usd for p in projections):
                self._note("beta_band_trim_too_small")
                return [], net, book
            exposure = sum(self._beta_delta(r, p["shares"],
                                            float(state.betas.get(r.ticker, 1.0)))
                           for r, p in zip(rows, projections))
            if abs(net + exposure) > band:
                self._note("beta_band")
                return [], net, book

        proposed = book + [
            {"q": float(r.win_prob), "cost": p["cost_usd"], "ticker": r.ticker,
             "direction": sim.direction_of(r.contract_type, int(r.outcome_index))}
            for r, p in zip(rows, projections)]
        if self._variance(proposed) > budget:
            self._note("variance_budget")
            return [], net, book

        for o in orders:
            o.reason = reason
        return orders, net + exposure, proposed
