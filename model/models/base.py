"""The model format: what a model receives, and what it returns.

A model is one file in `model/models/` with one class:

    class EdgeTaker(Model):
        name = "edge_taker"
        bar = TODAYS_BAR

        def decide(self, offers, book):
            return [Order(bet, usd=bet.max_usd) for bet in offers]

`offers` is every bet of this type that clears `self.bar` this cycle, best
return first. `book` is what the model already holds. A model does not know
which account it runs under or where the data comes from; the harness
(`sim.py`) caps each order by the bet's `max_usd` and by the account's cash,
fills it and does the accounting.

Directions and vol, for a hit market:

| bet              | direction | vol  |
|------------------|-----------|------|
| YES on hit_up    | up        | wild |
| NO on hit_down   | up        | calm |
| YES on hit_down  | down      | wild |
| NO on hit_up     | down      | calm |
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

YES, NO = "YES", "NO"
UP, DOWN = "up", "down"
CALM, WILD = "calm", "wild"


@dataclass(frozen=True)
class Bar:
    """What a bet must clear, per share, at the price paid.

    `value` is the win probability, less `yes_premium` on a YES. A bet clears
    when `value - cost >= min_ev` and `(value - cost) / cost >= min_roi`;
    above `dear_price` the EV and ROI floors are `dear_min_ev` and
    `dear_min_roi` instead. A floor at or below -1 is no floor.
    """

    min_ev: float = 0.03
    min_roi: float = 0.08
    dear_price: float = 0.90
    dear_min_ev: float = 0.0
    dear_min_roi: float = 0.02
    yes_premium: float = 0.02

    def value(self, win_prob: float, side: str) -> float:
        return win_prob - (self.yes_premium if side == YES else 0.0)

    def floors(self, ask: float) -> tuple[float, float]:
        if ask > self.dear_price:
            return self.dear_min_ev, self.dear_min_roi
        return self.min_ev, self.min_roi

    def clears(self, value: float, cost: float, ask: float) -> bool:
        if not (math.isfinite(value) and 0.0 < ask and cost < 1.0):
            return False
        ev_floor, roi_floor = self.floors(ask)
        ev = value - cost
        return ev >= ev_floor and ev / cost >= roi_floor

    def limit(self, value: float, ask: float) -> float:
        """The highest all-in cost per share that still clears this bar.

        Judged with the floors of the best ask, so every deeper share bought
        clears the same floors the best one did.
        """
        ev_floor, roi_floor = self.floors(ask)
        by_roi = value / (1.0 + roi_floor) if roi_floor > -1.0 else math.inf
        return min(value - ev_floor, by_roi)


#: Today's bar, unchanged: EV >= 3c and return >= 8%; above a 90c ask,
#: return >= 2%; every YES charged a 2c risk premium first. Open in CLAUDE.md:
#: only the risk premium is approved (2026-10-09). This is the one place it is
#: written down.
TODAYS_BAR = Bar()

#: No bar at all: every priced bet is on offer, at any price below $1.
ANY_PRICE = Bar(min_ev=-1.0, min_roi=-1.0, dear_min_ev=-1.0, dear_min_roi=-1.0,
                yes_premium=0.0)


@dataclass(frozen=True)
class Bet:
    """One side of one market, on offer now. Prices are per $1 share.

    `ask` is the best ask and `cost` the best ask plus the venue's fee.
    `value` is `win_prob` less the bar's YES premium; `ev` and `roi` are judged
    on it at the best ask. `max_usd` is the most of the book that still clears
    the bar. `limit` is the highest all-in cost per share that does.
    """

    market_id: str
    ticker: str
    type: str
    side: str                     # YES or NO
    win_prob: float
    value: float
    ask: float
    cost: float
    deadline: object              # pd.Timestamp
    max_usd: float
    limit: float
    direction: str | None         # 'up', 'down' or None
    vol: str | None               # 'calm', 'wild' or None

    @property
    def ev(self) -> float:
        return self.value - self.cost

    @property
    def roi(self) -> float:
        return self.ev / self.cost


@dataclass(frozen=True)
class Order:
    """Up to `usd` of `bet`. `limit`, if given, lowers the highest all-in cost
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
    """What a model holds, under whatever account it runs in.

    `cash` is what the account lets it spend now (infinite under `max`).
    `settled` are the holdings that have resolved, with their PnL.
    """

    open: list[Holding] = field(default_factory=list)
    settled: list[Holding] = field(default_factory=list)
    cash: float = math.inf

    def usd_by_direction(self) -> dict:
        return _total(self.open, "direction", (UP, DOWN))

    def usd_by_vol(self) -> dict:
        return _total(self.open, "vol", (CALM, WILD))


def _total(holdings: list[Holding], attr: str, keys: tuple) -> dict:
    return {k: sum(h.cost for h in holdings if getattr(h, attr) == k) for k in keys}


class Model:
    """Subclass, set `name` and `bar`, and write `decide`."""

    name = ""
    bar = TODAYS_BAR

    def decide(self, offers: list[Bet], book: Book) -> list[Order]:
        raise NotImplementedError
