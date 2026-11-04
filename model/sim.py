"""Replay one model on one type of bet under one account.

The harness turns a type's books and fair values into `Bet`s, hands them to
the model with the model's `Book`, caps the orders it returns by the bet's
`max_usd` and the account's cash, fills them against the real ladder, and
settles what resolves. Each model x type x account is its own replay, so one
type's capital never competes with another's. Metrics, reports and ledgers of
a finished run are `results.py`.

Three things here are load-bearing:

* **Only the cycle being replayed is read.** A model sees offers built from
  that cycle's books and fair values and nothing else.
* **Fills walk the real ladder, not the mid.** The token's own asks by
  `depth_rank`, at most `TAKE_FRACTION` of each level, stopping at the first
  level whose all-in cost reaches the limit. A fill counts if the book had the
  size when we looked (CLAUDE.md, Decided).
* **One market, one side, once.** A market already held on either side, or
  held until it settled, is not offered again (MODELS 4.2: no averaging down).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import data
from models import NO, YES, Bet, Book, Holding, Order

#: Never take more than this share of a level: the rest may be gone by the
#: time an order lands.
TAKE_FRACTION = 0.5
#: Polymarket's minimum order. A smaller "fill" could not have been placed.
MIN_FILL_USD = 1.0
SIDE_INDEX = {YES: 0, NO: 1}
EASTERN = "America/New_York"


# ---------------------------------------------------------------- accounts


class Account:
    """How much a model may spend. The harness asks before every order."""

    name = ""

    def cash(self) -> float:
        raise NotImplementedError

    def start_cycle(self, now: pd.Timestamp, period: str) -> None:
        pass

    def spend(self, usd: float) -> None:
        pass

    def receive(self, usd: float) -> None:
        pass


class MaxAccount(Account):
    """No ceiling: every bet is as large as the book allows. Capital used is
    the sum of the bets."""

    name = "max"

    def cash(self) -> float:
        return math.inf


class CompoundAccount(Account):
    """Starts at `start` and reinvests what comes back."""

    name = "compound"

    def __init__(self, start: float = 100.0):
        self.balance = float(start)

    def cash(self) -> float:
        return self.balance

    def spend(self, usd: float) -> None:
        self.balance -= usd

    def receive(self, usd: float) -> None:
        self.balance += usd


class BudgetAccount(Account):
    """`per_period` dollars each period of the type (day, week from Monday, or
    calendar month, in New York time). Unspent budget does not roll over, and
    payouts do not add to it."""

    name = "budget"

    def __init__(self, per_period: float):
        self.per_period = float(per_period)
        self.left = float(per_period)
        self._period = None

    def cash(self) -> float:
        return self.left

    def start_cycle(self, now: pd.Timestamp, period: str) -> None:
        key = period_key(now, period)
        if key != self._period:
            self._period, self.left = key, self.per_period

    def spend(self, usd: float) -> None:
        self.left -= usd


def make_account(name: str, start: float = 100.0, per_period: float | None = None) -> Account:
    if name == "max":
        return MaxAccount()
    if name == "compound":
        return CompoundAccount(start)
    if name == "budget":
        if per_period is None:
            raise ValueError("the budget account needs --per-period")
        return BudgetAccount(per_period)
    raise ValueError(f"unknown account {name!r}; one of max, compound, budget")


def period_key(when, period: str) -> str:
    """The day, week (starting Monday) or month `when` falls in, New York time."""
    local = pd.Timestamp(when).tz_convert(EASTERN)
    if period == "daily":
        return local.date().isoformat()
    if period == "monthly":
        return f"{local.year:04d}-{local.month:02d}"
    return (local.date() - dt.timedelta(days=local.weekday())).isoformat()


# ---------------------------------------------------------------- the ledger


@dataclass
class Fill:
    cycle_id: str
    filled_at: pd.Timestamp
    market_id: str
    side: str
    depth_rank: int
    price: float
    shares: float
    fee_usd: float
    cost_usd: float
    ev_usd: float
    level_size: float


@dataclass
class Position:
    """A bet bought, and, once it resolves, what it paid."""

    market_id: str
    side: str
    ticker: str
    type: str
    contract_type: str
    level: float
    deadline: pd.Timestamp
    period: str
    direction: str | None
    vol: str | None
    entry_cycle_id: str
    entry_at: pd.Timestamp
    win_prob: float
    shares: float = 0.0
    cost_usd: float = 0.0
    fee_usd: float = 0.0
    #: Expected profit at entry, on the bar's value (win probability less any
    #: YES premium) against the all-in cost actually paid.
    ev_usd: float = 0.0
    fills: list[Fill] = field(default_factory=list)
    result: str | None = None
    payout_usd: float | None = None
    settled_at: pd.Timestamp | None = None

    @property
    def settled(self) -> bool:
        return self.settled_at is not None

    @property
    def pnl_usd(self) -> float | None:
        return None if self.payout_usd is None else self.payout_usd - self.cost_usd

    def holding(self) -> Holding:
        return Holding(market_id=self.market_id, ticker=self.ticker, side=self.side,
                       direction=self.direction, vol=self.vol, shares=self.shares,
                       cost=self.cost_usd, pnl=self.pnl_usd)


# ------------------------------------------------------------------ offers


def walk_ladder(ladder: pd.DataFrame, limit: float, budget_usd: float, fee) -> list[dict]:
    """The levels an order would take, best first.

    Three stops, in this order at every level: the limit (an all-in cost at or
    above `limit` ends the walk - a deeper level is only ever worse), the
    `TAKE_FRACTION` of that level's size, and the money left. The whole walk is
    discarded if it comes to less than `MIN_FILL_USD`.
    """
    taken: list[dict] = []
    left = budget_usd
    for level in ladder.sort_values("depth_rank").itertuples():
        price = float(level.price)
        if not (0.0 < price < 1.0):
            continue
        cost_per_share = price + fee(price)
        if cost_per_share >= limit:
            break
        shares = min(TAKE_FRACTION * float(level.size), left / cost_per_share)
        if shares <= 0:
            break
        spend = shares * cost_per_share
        taken.append({"depth_rank": int(level.depth_rank), "price": price, "shares": shares,
                      "fee_usd": fee(price) * shares, "cost_usd": spend,
                      "level_size": float(level.size)})
        left -= spend
        if left <= 1e-9:
            break
    if sum(t["cost_usd"] for t in taken) < MIN_FILL_USD:
        return []
    return taken


def opposite(direction: str | None) -> str | None:
    return {"up": "down", "down": "up"}.get(direction)


def make_offers(bet_type, bar, priced: pd.DataFrame, asks: pd.DataFrame,
                exclude: set[str]) -> tuple[list[Bet], dict]:
    """Every bet of the cycle that clears `bar`, best return first, with the
    ladders behind them. Markets in `exclude` (already held) are left out."""
    if priced.empty or asks.empty:
        return [], {}
    asks = asks.assign(market_id=asks["market_id"].astype(str))
    ladders = {key: frame for key, frame in asks.groupby(["market_id", "outcome_index"])}
    offers, behind = [], {}
    for row in priced.itertuples():
        if row.market_id in exclude or not np.isfinite(row.fair):
            continue
        yes_vol = row.yes_vol if isinstance(row.yes_vol, str) else None
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
            cost = ask + bet_type.fee(ask)
            if not bar.clears(value, cost, ask):
                continue
            limit = bar.limit(value, ask)
            max_usd = sum(t["cost_usd"]
                          for t in walk_ladder(ladder, limit, math.inf, bet_type.fee))
            if max_usd <= 0:
                continue
            offers.append(Bet(
                market_id=row.market_id, ticker=row.ticker, type=bet_type.name, side=side,
                win_prob=win_prob, value=value, ask=ask, cost=cost, deadline=row.deadline,
                max_usd=max_usd, limit=limit,
                direction=row.yes_direction if side == YES else opposite(row.yes_direction),
                vol=yes_vol if side == YES else ("calm" if yes_vol == "wild" else None)))
            behind[(row.market_id, side)] = (ladder, row)
    offers.sort(key=lambda b: -b.roi)
    return offers, behind


# --------------------------------------------------------------- resolution


def payout_per_share(result, outcome_prices, side: str) -> float | None:
    """1, 0 or 0.5 per share. `None` means the market has not really resolved.

    MODELS 4.3's 50-50 case is a null `result` with `outcome_prices` at
    0.5/0.5; a null `result` with anything else is a pending UMA dispute
    (MODELS section 6) and must not be booked as a loss.
    """
    if isinstance(result, str) and result in ("Yes", "No"):
        return 1.0 if (result == "Yes") == (side == YES) else 0.0
    prices = outcome_prices
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (ValueError, TypeError):
            return None
    if isinstance(prices, (list, tuple)) and len(prices) == 2:
        try:
            values = [float(x) for x in prices]
        except (TypeError, ValueError):
            return None
        if abs(values[0] - 0.5) < 1e-9 and abs(values[1] - 0.5) < 1e-9:
            return 0.5
    return None


def settle(open_positions: dict, resolutions: pd.DataFrame, account: Account) -> list[Position]:
    """Close every open position whose market has resolved in `resolutions`."""
    if resolutions is None or resolutions.empty:
        return []
    by_market = (resolutions.assign(market_id=resolutions["market_id"].astype(str))
                 .drop_duplicates("market_id").set_index("market_id"))
    done = []
    for key in list(open_positions):
        market_id, side = key
        if market_id not in by_market.index:
            continue
        row = by_market.loc[market_id]
        payout = payout_per_share(row.get("result"), row.get("outcome_prices"), side)
        if payout is None:
            continue
        position = open_positions.pop(key)
        position.result = str(row.get("result"))
        position.settled_at = pd.Timestamp(row["settled_at_utc"])
        position.payout_usd = payout * position.shares
        account.receive(position.payout_usd)
        done.append(position)
    return done


# ------------------------------------------------------------- the replay


@dataclass
class Result:
    model: str
    type: str
    account: str
    period: str
    cycles: list[str]
    positions: list[Position]
    seconds: float
    settled_through: str
    skipped: dict
    note: str = ""

    @property
    def fills(self) -> list[Fill]:
        return [f for p in self.positions for f in p.fills]


def cycle_time(books: pd.DataFrame, cycle_id: str) -> pd.Timestamp:
    """The cycle's clock: the newest fetch in it, else the id's own stamp.

    `fetched_at_utc`, not `book_timestamp_utc`: the latter is the venue's last
    change to a book and can be days old.
    """
    if len(books) and books["fetched_at_utc"].notna().any():
        return pd.Timestamp(books["fetched_at_utc"].max())
    return pd.Timestamp(data.cycle_start(cycle_id))


def latest_settlement(bet_type) -> str:
    """The newest settlement on disk: how far a finished run may be settled."""
    frame = bet_type.resolutions("2100-01-01")
    if frame.empty:
        return "2100-01-01"
    return str(pd.Timestamp(frame["settled_at_utc"].max()))


def run(model, bet_type, account: Account, dates: list[str],
        settle_through: str | None = None) -> Result:
    """Replay every cycle of `dates` for one model on one type under one account.

    Inside a cycle: settle what has resolved by now, offer, let the model
    decide, fill. Settling first is what lets a model spend cash that came
    back this morning.
    """
    started = time.time()
    cycles = bet_type.cycles(dates)
    open_positions: dict[tuple[str, str], Position] = {}
    closed: list[Position] = []

    for cycle_id in cycles:
        books = bet_type.books(cycle_id)
        now = cycle_time(books, cycle_id)
        asks = books[books["side"] == "ask"] if len(books) else books
        closed += settle(open_positions, bet_type.resolutions(now), account)
        account.start_cycle(now, bet_type.period)

        held = {m for m, _ in open_positions} | {p.market_id for p in closed}
        offers, behind = make_offers(bet_type, model.bar, bet_type.priced(cycle_id), asks, held)
        if not offers:
            continue
        book = Book(open=[p.holding() for p in open_positions.values()],
                    settled=[p.holding() for p in closed], cash=account.cash())
        for order in model.decide(offers, book) or []:
            _fill(order, behind, open_positions, held, account, bet_type, cycle_id, now)

    # The decisions are over; what has settled since is the outcome, not
    # lookahead. MODELS 4.3: headline numbers are resolved-only.
    through = settle_through or latest_settlement(bet_type)
    closed += settle(open_positions, bet_type.resolutions(through), account)

    return Result(model=model.name, type=bet_type.name, account=account.name,
                  period=bet_type.period, cycles=cycles,
                  positions=closed + list(open_positions.values()),
                  seconds=time.time() - started, settled_through=str(through),
                  skipped=dict(bet_type.skipped), note=bet_type.note)


def _fill(order: Order, behind: dict, open_positions: dict, held: set, account: Account,
          bet_type, cycle_id: str, now: pd.Timestamp) -> None:
    bet = order.bet
    key = (bet.market_id, bet.side)
    if key not in behind or bet.market_id in held:
        return                     # not offered this cycle, or already bought
    ladder, row = behind[key]
    usd = min(order.usd, bet.max_usd, account.cash())
    limit = bet.limit if order.limit is None else min(bet.limit, order.limit)
    taken = walk_ladder(ladder, limit, usd, bet_type.fee) if usd > 0 else []
    if not taken:
        return
    position = Position(
        market_id=bet.market_id, side=bet.side, ticker=bet.ticker, type=bet.type,
        contract_type=str(row.contract_type), level=float(row.level),
        deadline=row.deadline, period=str(row.period or ""), direction=bet.direction,
        vol=bet.vol, entry_cycle_id=cycle_id, entry_at=now, win_prob=bet.win_prob)
    for t in taken:
        fill = Fill(cycle_id=cycle_id, filled_at=now, market_id=bet.market_id, side=bet.side,
                    ev_usd=bet.value * t["shares"] - t["cost_usd"], **t)
        position.fills.append(fill)
        position.shares += fill.shares
        position.cost_usd += fill.cost_usd
        position.fee_usd += fill.fee_usd
        position.ev_usd += fill.ev_usd
        account.spend(fill.cost_usd)
    open_positions[key] = position
    held.add(bet.market_id)
