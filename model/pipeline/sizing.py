"""Step 8 - sizing: the model. Which offers to take, and how much of each.

A model receives this cycle's offers (bets that already clear its bar, best
return first) and its `Book`, and returns `Order`s. It never knows its
account or where the data comes from; the harness caps each order by the
bet's `max_usd` and the account's cash.

| variant | does | decided / open |
|---|---|---|
| `edge_taker` | takes every offer, as large as the book allows | the MVP model |
| `random_control` | `orders_per_cycle` (12) bets drawn from every priced bet, at most `slip` (2c) over the best ask | the control; use it with `bar: any_price` |

Directions and vol, for a hit market: YES on hit_up and NO on hit_down lean
`up`; the other two `down`. NO is `calm`, YES `wild`.

A new model is one new class here, registered with `@variant("sizing", name)`.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from pipeline.core import variant
from pipeline.offers import Bet


@dataclass(frozen=True)
class Order:
    """Up to `usd` of `bet`; `limit`, if given, lowers the highest all-in cost
    per share the harness will pay below the bet's own."""

    bet: Bet
    usd: float
    limit: float | None = None


@dataclass(frozen=True)
class Holding:
    """A bet held, or held until it settled (`pnl` is then set)."""

    market_id: str
    ticker: str
    side: str
    direction: str | None
    vol: str | None
    shares: float
    cost: float
    pnl: float | None = None


@dataclass
class Book:
    """What a model holds, and the cash its account allows now."""

    open: list[Holding] = field(default_factory=list)
    settled: list[Holding] = field(default_factory=list)
    cash: float = math.inf

    def usd_by(self, attr: str, keys: tuple) -> dict:
        return {k: sum(h.cost for h in self.open if getattr(h, attr) == k) for k in keys}

    def usd_by_direction(self) -> dict:
        return self.usd_by("direction", ("up", "down"))

    def usd_by_vol(self) -> dict:
        return self.usd_by("vol", ("calm", "wild"))


class Model:
    def __init__(self, **params):
        self.params = params

    def decide(self, offers: list[Bet], book: Book) -> list[Order]:
        raise NotImplementedError


@variant("sizing", "edge_taker")
class EdgeTaker(Model):
    def decide(self, offers, book):
        return [Order(bet, usd=bet.max_usd) for bet in offers]


@variant("sizing", "random_control")
class RandomControl(Model):
    def __init__(self, orders_per_cycle: int = 12, slip: float = 0.02, seed: int = 20260918):
        super().__init__(orders_per_cycle=orders_per_cycle, slip=slip, seed=seed)
        self._rng = random.Random(seed)

    def decide(self, offers, book):
        pool = [bet for bet in offers if 0.005 < bet.ask < 0.995]
        picks = self._rng.sample(pool, min(self.params["orders_per_cycle"], len(pool)))
        return [Order(bet, usd=bet.max_usd, limit=bet.cost + self.params["slip"])
                for bet in picks]
