"""`edge_taker`: take every bet that clears the bar.

No portfolio rule at all, which is the point: this model measures whether the
mispricing exists and survives costs, not whether a sensible book can be
built from it. Its concentration and its one-sidedness are measurements, not
recommendations.

The only caps are the per-market ones ($150 a YES market, $500 a NO market)
and the cash. The per-ticker and per-direction caps are lifted to 100%.
"""
from __future__ import annotations

from models.bets import Bet, Model
from sim import Params


class EdgeTaker(Model):
    name = "edge_taker"
    params = Params(ticker_cap_frac=1.0, direction_cap_frac=1.0)

    def decide(self, offers: list[Bet]) -> None:
        # Best ROI first: when the cash runs out it runs out on the worst bet.
        for bet in offers:
            if not bet.clears(self.min_ev, self.min_roi):
                continue
            self.take_bet(bet, usd=self.cap(bet), reason="edge")
