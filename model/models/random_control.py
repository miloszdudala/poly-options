"""`random_control`: no signal at all (MODELS 4.5's control).

Each cycle it buys `ORDERS_PER_CYCLE` bets drawn uniformly from every priced
bet on offer, at no more than two cents above the best ask's all-in cost. If
it makes money, the window drifted and no model here has demonstrated an
edge, which is the only reason to run it.
"""
from __future__ import annotations

import random

from models import ANY_PRICE, Bet, Book, Model, Order

ORDERS_PER_CYCLE = 12
SLIP = 0.02
SEED = 20260918


class RandomControl(Model):
    name = "random_control"
    bar = ANY_PRICE

    def __init__(self):
        self._rng = random.Random(SEED)

    def decide(self, offers: list[Bet], book: Book) -> list[Order]:
        pool = [bet for bet in offers if 0.005 < bet.ask < 0.995]
        picks = self._rng.sample(pool, min(ORDERS_PER_CYCLE, len(pool)))
        return [Order(bet, usd=bet.max_usd, limit=bet.cost + SLIP) for bet in picks]
