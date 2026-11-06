"""Steps 6 and 7 - fee and bar: what a share costs, and which bets are worth it.

**fee** (what a share costs on top of the price), `fee(price, market_id, now)`:

| variant | fee per share | decided / open |
|---|---|---|
| `polymarket` | `rate * (p (1 - p)) ** exponent`, the market's own schedule at that moment (`poly_markets.fee_schedule`); `rate` 0.04, exponent 1 for a market never recorded | decided ("the true venue fees"; docs.polymarket.com/trading/fees) |
| `kalshi` | `rate * p (1 - p)`, `rate` 0.07 | the API's quadratic schedule, multiplier 1 (series/fee_changes, read 2026-11-04) |

**bar** (what a bet must clear, per share, at the price paid). `value` is the
win probability less `yes_premium` on a YES. A bet clears when
`value - cost >= min_ev` and `(value - cost) / cost >= min_roi`; above
`dear_price` the floors are `dear_min_ev` / `dear_min_roi`. A floor at or below
-1 is no floor. Open in CLAUDE.md: only `yes_premium` (2c) is approved.

An **offer** is a bet that clears the bar, with the most of the book that still
clears it (`max_usd`, walking the ladder as `fills.py` would) and the highest
all-in cost per share that does (`limit`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import store
from pipeline.core import variant
from pipeline.fills import walk_ladder

YES, NO = "YES", "NO"
SIDE_INDEX = {YES: 0, NO: 1}


@variant("fee", "polymarket")
def polymarket_fee(params: dict):
    fallback = params.get("rate", 0.04)

    def fee(price, market_id=None, now=None):
        known = store.poly_fee_at(market_id, now) if market_id is not None else None
        rate, exponent = known if known is not None else (fallback, 1.0)
        return rate * (price * (1.0 - price)) ** exponent
    return fee


@variant("fee", "kalshi")
def kalshi_fee(params: dict):
    rate = params.get("rate", 0.07)
    return lambda price, market_id=None, now=None: rate * price * (1.0 - price)


@dataclass(frozen=True)
class Bar:
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
        """The highest all-in cost per share that still clears, judged on the
        best ask's floors, so every deeper share clears what the best one did."""
        ev_floor, roi_floor = self.floors(ask)
        by_roi = value / (1.0 + roi_floor) if roi_floor > -1.0 else math.inf
        return min(value - ev_floor, by_roi)


@dataclass(frozen=True)
class Bet:
    """One side of one market, on offer now. Prices are per $1 share."""

    market_id: str
    ticker: str
    side: str                     # YES or NO
    win_prob: float
    value: float                  # win_prob less the bar's YES premium
    ask: float
    cost: float                   # best ask + fee
    deadline: object
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


def opposite(direction):
    return {"up": "down", "down": "up"}.get(direction)


def make_offers(priced: pd.DataFrame, asks: pd.DataFrame, venue_fee, bar: Bar, fills: dict,
                exclude: set[str], now=None) -> tuple[list[Bet], dict]:
    """Every bet of the cycle that clears `bar`, best return first, with the
    ladders behind them. Markets in `exclude` (already held) are left out."""
    if priced.empty or asks.empty:
        return [], {}
    asks = asks.assign(market_id=asks["market_id"].astype(str))
    ladders = {key: frame for key, frame in asks.groupby(["market_id", "outcome_index"])}
    offers, behind = [], {}
    for row in priced[priced["tradeable"]].itertuples():
        if row.market_id in exclude or not np.isfinite(row.fair):
            continue
        yes_vol = row.yes_vol if isinstance(row.yes_vol, str) else None

        def fee(price, market_id=row.market_id):
            return venue_fee(price, market_id, now)

        for side in (YES, NO):
            ladder = ladders.get((row.market_id, SIDE_INDEX[side]))
            if ladder is None:
                continue
            top = ladder[ladder["depth_rank"] == 0]
            if top.empty:
                continue
            ask = float(top["price"].iloc[0])
            if not 0.0 < ask < 1.0:
                continue
            win_prob = row.fair if side == YES else 1.0 - row.fair
            value = bar.value(win_prob, side)
            cost = ask + fee(ask)
            if not bar.clears(value, cost, ask):
                continue
            limit = bar.limit(value, ask)
            max_usd = sum(t["cost_usd"] for t in walk_ladder(ladder, limit, math.inf, fee, **fills))
            if max_usd <= 0:
                continue
            offers.append(Bet(
                market_id=row.market_id, ticker=row.ticker, side=side, win_prob=win_prob,
                value=value, ask=ask, cost=cost, deadline=row.deadline, max_usd=max_usd,
                limit=limit,
                direction=row.yes_direction if side == YES else opposite(row.yes_direction),
                vol=yes_vol if side == YES else ("calm" if yes_vol == "wild" else None)))
            behind[(row.market_id, side)] = (ladder, row)
    offers.sort(key=lambda b: -b.roi)
    return offers, behind
