"""Polymarket weekly "will X hit $B" markets on stocks."""
from bet_types import PolymarketHit


class PolyHitWeeklyStock(PolymarketHit):
    name = "poly-hit-weekly-stock"
    period = "weekly"
