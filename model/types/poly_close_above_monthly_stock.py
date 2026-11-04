"""Polymarket monthly "will X close above $K" markets on stocks, from the call spread."""
from _chain import ChainPriced


class PolyCloseAboveMonthlyStock(ChainPriced):
    name = "poly-close-above-monthly-stock"
    contract_type = "close_above"
    period = "monthly"

    def fair_yes(self, above, market):
        p = above(market["strike"])
        return 1.0 - p if market.get("direction") == "below" else p

    def yes_direction(self, term):
        return "down" if term.get("direction") == "below" else "up"
