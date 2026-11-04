"""Polymarket weekly "will X close above $K" markets on stocks, from the call spread."""
from _chain import ChainPriced


class PolyCloseAboveWeeklyStock(ChainPriced):
    name = "poly-close-above-weekly-stock"
    contract_type = "close_above"
    period = "weekly"

    def fair_yes(self, above, market):
        p = above(market["strike"])
        return 1.0 - p if market.get("direction") == "below" else p

    def yes_direction(self, term):
        return "down" if term.get("direction") == "below" else "up"
