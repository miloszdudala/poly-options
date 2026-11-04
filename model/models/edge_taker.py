"""`edge_taker`: take every bet that clears the bar, as large as the book allows.

No portfolio rule at all: this model measures whether the mispricing exists
and survives costs, not whether a sensible book can be built from it. Its
concentration and its one-sidedness are measurements, not recommendations.
"""
from __future__ import annotations

from models import TODAYS_BAR, Bet, Book, Model, Order


class EdgeTaker(Model):
    name = "edge_taker"
    bar = TODAYS_BAR

    def decide(self, offers: list[Bet], book: Book) -> list[Order]:
        # Best return first: when the cash runs out it runs out on the worst bet.
        return [Order(bet, usd=bet.max_usd) for bet in offers]
