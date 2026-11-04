"""Polymarket monthly "will X hit $B" markets on stocks."""
from bet_types import PolymarketHit


class PolyHitMonthlyStock(PolymarketHit):
    name = "poly-hit-monthly-stock"
    period = "monthly"
