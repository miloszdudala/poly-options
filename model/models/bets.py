"""What a model works with: bets on offer, bets held, and the bets it takes.

Nothing in here knows about Polymarket, order books, cycles or ledgers. The
translation between those and the objects below is `models/market.py`; a model
file reads only this module.

Vocabulary:

* a **Bet** is one side of one market that is on offer right now, with the
  model's probability that it wins and what it costs;
* a **Holding** is a bet already held (the harness filled it earlier);
* a **Model** looks at the bets on offer and its holdings, and calls
  `take_bet` for each one it wants, or `refuse` with the reason it does not.

Every bet a hit market offers is a YES ("the stock touches the barrier before
the deadline") or a NO ("it does not"). A bet *leans* up or down:

| bet              | wins if the stock | leans |
|------------------|-------------------|-------|
| YES on hit_up    | rises to it       | up    |
| NO on hit_down   | does not fall to it | up  |
| YES on hit_down  | falls to it       | down  |
| NO on hit_up     | does not rise to it | down |
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from sim import Params

YES, NO = "YES", "NO"
UP, DOWN = "up", "down"

#: Above this ask a bet is judged on a lower bar: EV >= 0 and ROI >= 2%
#: (MODELS 2: 3c of edge on a 95c NO is a 3% return, never available).
DEAR_PRICE = 0.90
DEAR_MIN_EV, DEAR_MIN_ROI = 0.0, 0.02


def lean_of(kind: str, side: str) -> str:
    """Which way a bet on `kind` ('hit_up' / 'hit_down') and `side` leans."""
    return UP if (kind == "hit_up") == (side == YES) else DOWN


@dataclass(frozen=True)
class Quote:
    """What the book would actually give for an order: shares, and their all-in cost."""

    shares: float
    cost: float


@dataclass(frozen=True)
class Bet:
    """One side of one market, on offer now.

    Prices are per share and pay $1 if the bet wins. `cost` is the best ask
    plus the fee. `value` is what the model thinks a share is worth: its win
    probability, minus the 2c risk premium on a YES (INSIGHTS 7.2). EV and ROI
    are judged on `value`, at the best ask.
    """

    market_id: str
    ticker: str
    kind: str                 # 'hit_up' or 'hit_down'
    side: str                 # YES or NO
    ask: float                # best ask
    cost: float               # best ask + fee, per share
    win_prob: float           # the model's probability that this side wins
    value: float              # win_prob, less the YES risk premium
    touch_prob: float         # the model's probability the barrier is touched
    fair_source: str
    _quote: Callable[[float, float], Quote] = field(repr=False, compare=False)

    @property
    def ev(self) -> float:
        """Expected profit per $1 share, at the best ask."""
        return self.value - self.cost

    @property
    def roi(self) -> float:
        return self.ev / self.cost

    @property
    def lean(self) -> str:
        return lean_of(self.kind, self.side)

    @property
    def dear(self) -> bool:
        return self.ask > DEAR_PRICE

    def bars(self, min_ev: float, min_roi: float) -> tuple[float, float]:
        """The EV and ROI a bet has to clear: the model's, or the dear-price ones."""
        return (DEAR_MIN_EV, DEAR_MIN_ROI) if self.dear else (min_ev, min_roi)

    def clears(self, min_ev: float, min_roi: float) -> bool:
        ev_bar, roi_bar = self.bars(min_ev, min_roi)
        return self.ev >= ev_bar and self.roi >= roi_bar

    def limit(self, min_ev: float, min_roi: float) -> float:
        """The highest all-in cost per share still worth paying at these bars.

        The harness buys deeper levels of the book only while each costs less
        than this, so every share bought clears the same bars the best one did.
        """
        ev_bar, roi_bar = self.bars(min_ev, min_roi)
        return min(self.value - ev_bar, self.value / (1.0 + roi_bar))

    def quote(self, usd: float, limit: float) -> Quote:
        """What `usd` would buy now, at no more than `limit` a share.

        Less than `usd` when the book is thin or a cap (per market, per ticker,
        per direction, cash) is in the way; nothing if the market is already
        held, since a market is bought once.
        """
        return self._quote(usd, limit)


@dataclass(frozen=True)
class Holding:
    """A bet already held."""

    market_id: str
    ticker: str
    kind: str
    side: str
    shares: float
    cost: float               # paid, fee included
    touch_prob: float | None  # the model's probability now; None once touched

    @property
    def lean(self) -> str:
        return lean_of(self.kind, self.side)

    @property
    def touched(self) -> bool:
        return self.touch_prob is None


@dataclass(frozen=True)
class Take:
    """A bet a model chose: up to `usd`, at no more than `limit` a share."""

    bet: Bet
    usd: float
    limit: float
    reason: str
    at: pd.Timestamp | None = None


class Model:
    """The shape every model has.

    A subclass sets `name` and `params` (its caps) and writes one method,
    `decide(offers)`: go through the bets on offer, best ROI first, and call
    `take_bet` or `refuse` for each. `self.holdings` is what is held at the
    start of the cycle. `self.taken` is every bet the model has ever chosen,
    and `self.refusals` counts this cycle's refusals by reason.
    """

    name = "model"
    #: Caps the harness enforces on every bet: $ per YES / NO market, share of
    #: the bankroll per ticker and per direction, and the bankroll itself.
    params = Params()

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08):
        self.min_ev = min_ev
        self.min_roi = min_roi
        self.holdings: list[Holding] = []
        self.taken: list[Take] = []
        self.refusals: dict[str, int] = {}
        self._this_cycle: list[Take] = []
        self._now: pd.Timestamp | None = None
        self._memory: dict = {}              # for models/market.py only

    # ---------------------------------------------------- what a model writes

    def decide(self, offers: list[Bet]) -> None:
        raise NotImplementedError

    # ------------------------------------------------------ what a model calls

    def cap(self, bet: Bet) -> float:
        """The per-market cap for this bet's side."""
        return self.params.cap_yes_usd if bet.side == YES else self.params.cap_no_usd

    def take_bet(self, bet: Bet, usd: float, reason: str, limit: float | None = None) -> None:
        """Ask for up to `usd` of `bet`, at no more than `limit` a share
        (default: the model's own bars)."""
        if limit is None:
            limit = bet.limit(self.min_ev, self.min_roi)
        take = Take(bet=bet, usd=usd, limit=limit, reason=reason, at=self._now)
        self._this_cycle.append(take)
        self.taken.append(take)

    def refuse(self, why: str) -> None:
        """Count a bet not taken, by reason (the report lists these)."""
        self.refusals[why] = self.refusals.get(why, 0) + 1

    def count(self, what: str) -> None:
        """Count something else worth reporting, e.g. hedges taken."""
        self.refusals[what] = self.refusals.get(what, 0) + 1

    def held_by_lean(self) -> dict[str, float]:
        """Dollars held on each lean.

        `sum`, not a loop of `+=`: Python's `sum` of floats is compensated
        and can differ in the last bit, which once decided whether a book
        exactly $150 lopsided against a $150 slack was hedged.
        """
        return {lean: sum(h.cost for h in self.holdings if h.lean == lean)
                for lean in (UP, DOWN)}

    # ----------------------------------------- the harness's entry, not model logic

    def propose(self, state):
        """Called by the harness once a cycle; see models/market.py."""
        from models import market
        return market.run_cycle(self, state)
