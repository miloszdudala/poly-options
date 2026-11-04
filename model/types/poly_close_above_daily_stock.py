"""Polymarket daily "will X close above $K" markets on stocks, from the call spread."""
from _chain import ChainPriced


class PolyCloseAboveDailyStock(ChainPriced):
    name = "poly-close-above-daily-stock"
    contract_type = "close_above"
    period = "daily"

    def fair_yes(self, above, market):
        p = above(market["strike"])
        return 1.0 - p if market.get("direction") == "below" else p

    def yes_direction(self, term):
        return "down" if term.get("direction") == "below" else "up"
