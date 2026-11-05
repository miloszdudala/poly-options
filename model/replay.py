"""Replay one config over a set of dates: the pipeline, cycle by cycle.

Inside a cycle: price the markets (steps 1-5, cached), settle what has
resolved by now (step 11; first, so cash that came back this morning can be
spent), build the offers (fee and bar, 6-7), let the model choose (8), fill
against the book (9) and book it in the account (10). Only the cycle being
replayed is read; a market held, or held until it settled, is not offered
again (MODELS 4.2: no averaging down).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

import store
from pipeline import cache, markets as markets_step
from pipeline.core import Cycle, NotReady, get
from pipeline.offers import Bar, make_offers
from pipeline.settle import settle
from pipeline.sizing import Book, Holding, Order


@dataclass
class Fill:
    cycle_id: str
    filled_at: pd.Timestamp
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
    #: Expected profit at entry: the bar's value (win probability less any YES
    #: premium) against the all-in cost actually paid.
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
        return Holding(self.market_id, self.ticker, self.side, self.direction, self.vol,
                       self.shares, self.cost_usd, self.pnl_usd)


@dataclass
class Result:
    name: str                      # the config
    model: str                     # its sizing variant
    account: str
    period: str
    cycles: list[str]
    positions: list[Position]
    seconds: float
    settled_through: str
    skipped: dict                  # gate reason -> markets it ruled out
    config: dict = field(default_factory=dict)

    @property
    def type(self) -> str:
        return self.name

    @property
    def fills(self) -> list[Fill]:
        return [f for p in self.positions for f in p.fills]


def latest_settlement(venue=None) -> str:
    """The newest settlement on disk: how far a finished run may be settled."""
    frame = venue.resolutions("2100-01-01") if venue is not None else store.resolutions_upto(
        "2100-01-01")
    return "2100-01-01" if frame.empty else str(pd.Timestamp(frame["settled_at_utc"].max()))


def _build(step: str, config: dict):
    """The variant a config names for `step`, built with its parameters."""
    params = dict(config[step])
    variant = get(step, params.pop("use"))
    return variant(params) if step == "fee" else variant(**params)


def run(config: dict, dates: list[str], settle_through: str | None = None,
        use_cache: bool = True) -> Result:
    started = time.time()
    types = markets_step.contract_types(config["markets"])
    period = config["markets"]["period"]
    fee = _build("fee", config)
    model = _build("sizing", config)
    account = _build("account", config)
    bar = Bar(**config["bar"])
    fills_cfg = dict(config["fills"])
    venue = _build("venue", config)
    cycles = venue.cycles(dates)
    replayed: list[str] = []
    open_positions: dict[tuple[str, str], Position] = {}
    closed: list[Position] = []
    ruled_out: dict[str, set] = {}

    for cycle_id in cycles:
        cycle = Cycle(cycle_id, types, venue)
        try:
            priced = cache.priced(cycle, config, use_cache)
        except NotReady:
            continue
        replayed.append(cycle_id)
        now = cycle.now
        closed += settle(open_positions, venue.resolutions(now), account)
        account.start_cycle(now, period)
        if priced.empty:
            continue
        for reason, ids in priced[priced["gate"].notna()].groupby("gate")["market_id"]:
            ruled_out.setdefault(reason, set()).update(ids)

        held = {m for m, _ in open_positions} | {p.market_id for p in closed}
        offers, behind = make_offers(priced, cycle.asks, fee, bar, fills_cfg, held, now)
        if not offers:
            continue
        book = Book(open=[p.holding() for p in open_positions.values()],
                    settled=[p.holding() for p in closed], cash=account.cash())
        for order in model.decide(offers, book) or []:
            _fill(order, behind, open_positions, held, account, fee, fills_cfg, cycle_id, now)

    # The decisions are over; what has settled since is the outcome, not
    # lookahead. MODELS 4.3: headline numbers are resolved-only.
    through = settle_through or latest_settlement(venue)
    closed += settle(open_positions, venue.resolutions(through), account)
    return Result(name=config["name"], model=config["sizing"]["use"], account=account.name,
                  period=period, cycles=replayed, positions=closed + list(open_positions.values()),
                  seconds=time.time() - started, settled_through=str(through),
                  skipped={reason: len(ids) for reason, ids in sorted(ruled_out.items())},
                  config=config)


def _fill(order: Order, behind: dict, open_positions: dict, held: set, account, fee,
          fills_cfg: dict, cycle_id: str, now: pd.Timestamp) -> None:
    from pipeline.fills import walk_ladder

    bet = order.bet
    key = (bet.market_id, bet.side)
    if key not in behind or bet.market_id in held:
        return                     # not offered this cycle, or already bought
    ladder, row = behind[key]
    usd = min(order.usd, bet.max_usd, account.cash())
    limit = bet.limit if order.limit is None else min(bet.limit, order.limit)
    taken = (walk_ladder(ladder, limit, usd, lambda p: fee(p, bet.market_id, now), **fills_cfg)
             if usd > 0 else [])
    if not taken:
        return
    position = Position(
        market_id=bet.market_id, side=bet.side, ticker=bet.ticker,
        contract_type=str(row.contract_type), level=float(row.level), deadline=row.deadline,
        period=str(row.period or ""), direction=bet.direction, vol=bet.vol,
        entry_cycle_id=cycle_id, entry_at=now, win_prob=bet.win_prob)
    for t in taken:
        fill = Fill(cycle_id=cycle_id, filled_at=now, ev_usd=bet.value * t["shares"] - t["cost_usd"],
                    **t)
        position.fills.append(fill)
        position.shares += fill.shares
        position.cost_usd += fill.cost_usd
        position.fee_usd += fill.fee_usd
        position.ev_usd += fill.ev_usd
        account.spend(fill.cost_usd)
    open_positions[key] = position
    held.add(bet.market_id)
