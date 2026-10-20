"""`direction_balanced`: hold about as much "stock goes up" as "stock goes down".

The user's proposal (2026-10-09). Every bet leans up or down (`models/bets.py`):
YES on hit_up and NO on hit_down lean up, YES on hit_down and NO on hit_up
lean down. Measured in dollars paid, one lean may exceed the other by at most
`balance_frac` (10%) of everything held, or `balance_slack` ($150) while the
book is small.

Two passes a cycle:

1. **Edges.** Every bet that clears the bar, best ROI first, up to the
   per-market cap or the room its lean has left, whichever is smaller.
2. **Hedges.** If the book is still lopsided past the slack (one side
   resolved, a market touched), bets on the light lean that clear a lower bar,
   EV >= 0, up to what makes the two leans level and no further.

What it does not balance is volatility: NO on both sides of a stock leans
neither way and still loses on a big move in either direction. That is
scenario_hedged's job; this model is the safeguard for direction.
"""
from __future__ import annotations

from models.bets import DOWN, UP, Bet, Model
from sim import Params

BALANCE_FRAC = 0.10


class DirectionBalanced(Model):
    name = "direction_balanced"
    params = Params(ticker_cap_frac=0.10, direction_cap_frac=1.0)

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08,
                 balance_slack: float = 150.0, balance_frac: float = BALANCE_FRAC,
                 hedge_min_ev: float | None = 0.0):
        super().__init__(min_ev, min_roi)
        self.balance_slack = balance_slack
        self.balance_frac = balance_frac
        self.hedge_min_ev = hedge_min_ev

    def decide(self, offers: list[Bet]) -> None:
        held = self.held_by_lean()                  # at the start of the cycle
        slack = max(self.balance_slack, self.balance_frac * (held[UP] + held[DOWN]))
        after = dict(held)                          # including this cycle's bets
        minimum = self.params.min_fill_usd
        chosen = set()

        # 1. Edges, each only as large as its lean has room for. The other
        #    lean counts at what is held, not at this cycle's bets, so a bet
        #    the harness may still refuse cannot make room on the other side.
        for bet in offers:
            if not bet.clears(self.min_ev, self.min_roi):
                continue
            other = DOWN if bet.lean == UP else UP
            room = held[other] + slack - after[bet.lean]
            if room < minimum:
                self.refuse("direction_balance")
                continue
            usd = min(self.cap(bet), room)
            got = bet.quote(usd, bet.limit(self.min_ev, self.min_roi))
            if got.cost < minimum:
                self.refuse("no_depth")
                continue
            self.take_bet(bet, usd=usd, reason="direction_balanced")
            after[bet.lean] += got.cost
            chosen.add((bet.market_id, bet.side))

        # 2. Hedges, only while the book held is lopsided past the slack.
        if self.hedge_min_ev is None or abs(held[UP] - held[DOWN]) <= slack:
            return
        light = UP if held[UP] < held[DOWN] else DOWN
        heavy = DOWN if light == UP else UP
        for bet in offers:
            if not bet.clears(self.hedge_min_ev, 0.0):
                continue
            if (bet.market_id, bet.side) in chosen or bet.lean != light:
                continue
            gap = after[heavy] - after[light]
            if gap < minimum:
                break
            usd = min(self.cap(bet), gap)
            limit = bet.limit(self.hedge_min_ev, 0.0)
            got = bet.quote(usd, limit)
            if got.cost < minimum:
                continue
            self.take_bet(bet, usd=usd, reason="direction_balanced_hedge", limit=limit)
            after[light] += got.cost
            self.count("hedges")
