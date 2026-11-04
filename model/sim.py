"""Replay, execute, resolve: the loop every model runs inside.

Metrics, reports and ledgers of a finished run are `results.py`.

MODELS section 4. The model sees one cycle at a time and answers with orders;
everything else - what fills, what it costs, what comes back, and what any of
it proves - happens here, identically for every model, so that a difference
between two reports is a difference between two models.

Three things in here are load-bearing and easy to get wrong:

* **No lookahead is enforced, not assumed.** A `CycleState` is built from
  frames already sliced to that cycle, and the only way to ask for another
  cycle is `state.books_at` / `state.fair_at`, which raise `LookaheadError` on
  anything at or after the current one. A model cannot reach the future by
  accident, and a model that reaches for it on purpose fails loudly.

* **Fills walk the real ladder, not the mid.** MODELS 4.2: the token's own
  asks by `depth_rank`, at most `take_fraction` of each level, stopping at the
  first level whose all-in cost breaches the order's limit. Never derive the NO
  ask from the YES ask; both sides are stored and they are not complements
  (NO ask = 1 - YES *bid*).

* **A fill counts if the book had the size when we looked** (decided; see
  CLAUDE.md). There is one ledger.

* **Capital is locked, not notional.** Cash paid for a 95c NO is real money
  unavailable for a month, so every position carries capital-days and the
  report divides by them. Return on notional flatters a book of NO positions
  by roughly the ratio of their prices.
"""
from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import data
import factors

YES, NO = 0, 1
#: The volatility risk premium, charged on every YES bought on the option
#: signal (decided 2026-10-09; INSIGHTS §7.2). Option prices include the price
#: of crash insurance: on the S&P 500, 1995-2026, implied vol sat 3.7 points
#: above the vol that followed on 83% of days, ~2-3 points in at-the-money
#: terms, which is ~2-5c of touch probability on our barriers. A YES priced off
#: options looks that much better than it is, so it has to clear the EV bar
#: *plus* this. NO is not credited with it: there it is payment for carrying
#: crash risk, not edge. A fixed number from 31 years, not fitted to our data.
YES_RISK_PREMIUM = 0.02

LONG_STOCK = "long_stock"     # hit_up YES, hit_down NO
SHORT_STOCK = "short_stock"   # hit_down YES, hit_up NO


class LookaheadError(RuntimeError):
    """A model asked for a cycle it cannot have seen yet."""


# ------------------------------------------------------------------ params


@dataclass(frozen=True)
class Params:
    """MODELS 4.4's defaults. Every model may override any of them."""

    bankroll: float = 10_000.0
    take_fraction: float = 0.5
    fee_rate: float = 0.04
    cap_yes_usd: float = 150.0
    cap_no_usd: float = 500.0
    ticker_cap_frac: float = 0.10
    direction_cap_frac: float = 0.40
    # Polymarket's own minimum order. Below it a "fill" is an accounting entry
    # that could not have been placed, and 0.3-share fills would otherwise
    # dominate the fill count on a book whose median top level is $23.
    min_fill_usd: float = 1.0
    # MODELS 4.2: one market, one direction, per lifetime, unless the fair has
    # moved more than this since the entry.
    reenter_fair_move: float = 0.10


def fee_per_share(price: float, fee_rate: float = 0.04) -> float:
    """MODELS 2: `0.04 * min(p, 1-p)`, charged once, on the price paid."""
    return fee_rate * min(price, 1.0 - price)


def all_in_cost(price: float, fee_rate: float = 0.04) -> float:
    return price + fee_per_share(price, fee_rate)


def direction_of(contract_type: str, outcome_index: int) -> str:
    """Which way a position leans on the underlying. MODELS 4.4.

    `hit_up` YES and `hit_down` NO both make money when the stock goes up, and
    a book of both is a long book however balanced its YES/NO split looks -
    which is the whole point of MODELS 5.2b's table.
    """
    up = contract_type == "hit_up"
    is_yes = outcome_index == YES
    return LONG_STOCK if (up == is_yes) else SHORT_STOCK


def quadrant_of(contract_type: str, outcome_index: int) -> str:
    """MODELS 5.2b's four quadrants, named as that table names them."""
    return f"{contract_type}_{'YES' if outcome_index == YES else 'NO'}"


# ------------------------------------------------------------ the ledger


@dataclass
class Order:
    """What a model asks for. The harness decides what it gets.

    `limit_price` is the maximum **all-in cost per share** (price plus fee),
    not a price: MODELS 4.2 stops the walk at the first level where
    `price + fee >= fair - min_edge`, and a model that also carries an ROI
    floor folds both into one number here. Expressing it as a cost is what
    keeps the stop rule in the harness instead of in four models.
    """

    market_id: str
    outcome_index: int
    max_usd: float
    limit_price: float
    reason: str
    fair: float = float("nan")
    fair_source: str = ""
    #: MODELS 4.2: "one market, one direction, per model, per lifetime (no
    #: averaging down), unless the model **explicitly** re-enters after fair
    #: moves by more than 10c". Opting in is the explicit part; the 10c is
    #: checked by the harness. No model in section 5 sets it.
    reenter: bool = False


@dataclass
class Fill:
    cycle_id: str
    filled_at: pd.Timestamp
    market_id: str
    outcome_index: int
    depth_rank: int
    price: float
    shares: float
    fee_usd: float
    cost_usd: float
    level_size: float
    limit_price: float
    fair: float
    fair_source: str
    reason: str


@dataclass
class Position:
    market_id: str
    outcome_index: int
    ticker: str
    contract_type: str
    barrier: float
    deadline: pd.Timestamp
    period: str
    entry_cycle_id: str
    entry_at: pd.Timestamp
    #: The fair the model actually traded on, and the source it came from.
    #: MODELS 4.5 scores "mean fair of positions vs hit rate"; recording the
    #: engine's `fair_K` here would score a forecaster a model may never have
    #: used.
    entry_fair: float
    fair_source: str = ""
    #: The engine's `fair_K` at entry, kept apart because MODELS 4.2's 10c
    #: re-entry test has to be evaluated by the harness at a later cycle and
    #: the harness cannot recompute a model's own signal.
    entry_fair_engine: float = float("nan")
    shares: float = 0.0
    cost_usd: float = 0.0
    fee_usd: float = 0.0
    fills: list[Fill] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, int]:
        return (self.market_id, self.outcome_index)

    @property
    def direction(self) -> str:
        return direction_of(self.contract_type, self.outcome_index)

    @property
    def quadrant(self) -> str:
        return quadrant_of(self.contract_type, self.outcome_index)

    def win_probability(self, fair: float) -> float:
        """The position's own fair: `q` for a YES, `1 - q` for a NO."""
        return fair if self.outcome_index == YES else 1.0 - fair


@dataclass
class Settlement:
    position: Position
    result: str
    settled_at: pd.Timestamp
    cash_back_at: pd.Timestamp
    payout_per_share: float
    payout_usd: float
    pnl_usd: float
    capital_days: float


class Portfolio:
    """Cash, open positions, and the exposure arithmetic the models size on."""

    def __init__(self, params: Params):
        self.params = params
        self.cash = params.bankroll
        self.open: dict[tuple[str, int], Position] = {}
        self.settled: list[Settlement] = []
        self.retired: dict[tuple[str, int], Position] = {}

    # -- exposure ---------------------------------------------------------

    def ticker_usd(self, ticker: str) -> float:
        return sum(p.cost_usd for p in self.open.values() if p.ticker == ticker)

    def direction_usd(self, direction: str) -> float:
        return sum(p.cost_usd for p in self.open.values() if p.direction == direction)

    def market_usd(self, market_id: str) -> float:
        return sum(p.cost_usd for p in self.open.values() if p.market_id == market_id)

    def held_direction(self, market_id: str) -> int | None:
        """Which side of a market is already held, if any. MODELS 4.2."""
        for (mid, outcome), _ in self.open.items():
            if mid == market_id:
                return outcome
        for (mid, outcome) in self.retired:
            if mid == market_id:
                return outcome
        return None


def model_fair(row, fair_source: str) -> float:
    """The number a model would put on a position, given where its fair came from.

    Screening is strict about a half-specified fair (`candidate_edges` refuses
    it); marking is lenient, because an open position whose ATM vol went
    missing this cycle is better marked on the strike vol than dropped from the
    book's value altogether. The asymmetry is deliberate: one decides whether
    to take risk, the other only decides what to call the risk already taken.
    """
    if row is None:
        return float("nan")
    k = float(row.get("fair_K", float("nan")))
    atm = float(row.get("fair_atm", float("nan")))
    if fair_source.startswith("min"):
        return float(np.fmin(k, atm))
    if fair_source.startswith("max"):
        return float(np.fmax(k, atm))
    return k


def _row_for(fair_rows: pd.DataFrame, market_id: str):
    if fair_rows is None or fair_rows.empty:
        return None
    hit = fair_rows[fair_rows["market_id"] == market_id]
    return None if hit.empty else hit.iloc[0]


# --------------------------------------------------------------- the state


class CycleState:
    """Everything a model is allowed to see at one cycle, and nothing else.

    Built by `Replay`, handed to `propose`, thrown away. The frames are the
    cycle's own rows; the accessors below are the only route to another cycle
    and they refuse to look forward.
    """

    def __init__(self, cycle_id: str, now: pd.Timestamp, books: pd.DataFrame,
                 fair: pd.DataFrame, resolutions: pd.DataFrame, portfolio: Portfolio,
                 betas: dict, spy_intraday: float, params: Params, loader):
        self.cycle_id = cycle_id
        self.now = now
        self.books = books
        self.fair = fair
        self.resolutions = resolutions
        self.portfolio = portfolio
        self.betas = betas
        self.spy_intraday = spy_intraday
        self.params = params
        self._loader = loader
        self._asks = None

    # -- the past is available, the future is not -------------------------

    def _check(self, cycle_id: str) -> None:
        if str(cycle_id) >= self.cycle_id:
            raise LookaheadError(
                f"{self.cycle_id} cannot see {cycle_id}: a model may only read "
                "cycles strictly before its own (MODELS 4.1)")

    def books_at(self, cycle_id: str) -> pd.DataFrame:
        self._check(cycle_id)
        return self._loader.books(cycle_id)

    # -- convenience the models share -------------------------------------

    @property
    def asks(self) -> pd.DataFrame:
        """Every ask level of the cycle, best price first within each token."""
        if self._asks is None:
            asks = self.books[self.books["side"] == "ask"]
            self._asks = asks.sort_values(["market_id", "outcome_index", "depth_rank"])
        return self._asks

    def best_asks(self) -> pd.DataFrame:
        """One row per (market_id, outcome_index): the top of the ask ladder."""
        top = self.asks[self.asks["depth_rank"] == 0]
        return top[["market_id", "outcome_index", "ticker", "contract_type",
                    "price", "size"]].rename(columns={"price": "ask", "size": "ask_size"})

    def best_bids(self) -> pd.DataFrame:
        bids = self.books[(self.books["side"] == "bid") & (self.books["depth_rank"] == 0)]
        return bids[["market_id", "outcome_index", "price"]].rename(columns={"price": "bid"})

    def ladder(self, market_id: str, outcome_index: int) -> pd.DataFrame:
        a = self.asks
        return a[(a["market_id"] == market_id) & (a["outcome_index"] == outcome_index)]


# ------------------------------------------------------------- the signal


def candidate_edges(state: CycleState, *, min_ev: float = 0.03, min_roi: float = 0.08,
                    high_price: float = 0.90, high_price_min_roi: float = 0.02,
                    high_price_min_ev: float = 0.0,
                    exclude_deep_otm: bool = True,
                    require_chain: bool = True,
                    yes_premium: float = YES_RISK_PREMIUM) -> pd.DataFrame:
    """MODELS 5.1's bar, evaluated on both tokens of every priced market.

    The conservative fair differs by side and that is not symmetry for its own
    sake: a YES is worth `q` so the cautious `q` is the smaller of the two
    engine numbers, and a NO is worth `1 - q` so the cautious `q` is the
    larger. Taking `min` on both sides would make every NO look better than it
    is, on exactly the markets where the two vols disagree most.

    MODELS 2's second bar - ROI >= 2% above 90c instead of EV >= 3c - is here
    rather than in the models because A, B and B-simple share this signal
    verbatim; only C replaces it.

    **YES pays the risk premium** (`YES_RISK_PREMIUM`, 2c): a YES's EV, ROI and
    limit are computed on `win_prob - yes_premium`. `fair` and `win_prob` stay
    the engine's numbers, so the reliability tables still score the engine.

    Returns one row per tradeable (market_id, outcome_index) with the edge at
    the **top of the ask ladder**. The harness re-checks every deeper level
    against `limit_price`; nothing here is averaged across levels.
    """
    fair = state.fair
    if fair.empty:
        return pd.DataFrame()
    cols = ["market_id", "ticker", "contract_type", "barrier", "fair_K", "fair_atm",
            "fair_cont", "delta", "spot", "deep_otm", "no_chain",
            "touch_pending", "stale_book", "deadline_utc", "period",
            "earnings_gap", "vol_source", "quote_age_s"]
    fair = fair[[c for c in cols if c in fair.columns]].copy()
    fair = fair[~fair["touch_pending"].fillna(False).astype(bool)]
    if exclude_deep_otm:
        fair = fair[~fair["deep_otm"].fillna(False).astype(bool)]
    if require_chain:
        fair = fair[~fair["no_chain"].fillna(False).astype(bool)]
    if "quote_age_s" in fair:
        # The chain is fetched two to three minutes after the books and is 15
        # minutes delayed, so its prints normally precede the book by ~13
        # minutes. When a slow tick reverses that (2026-09-16 13:40Z: chain
        # fetched 26 minutes after the books, prints 10 minutes newer than the
        # book), the fair value knows something the book did not, and a fill
        # against that book is not one a taker could have made.
        fair = fair[~(fair["quote_age_s"] < 0)]
    if "earnings_gap" in fair and "vol_source" in fair:
        # An earnings report between the deadline and the expiry the vol came
        # from puts a jump in the vol the barrier never sees. The engine
        # reprices off the pre-earnings expiry where one exists; where none
        # does the fair is biased high and the option signal must not use it.
        unrepaired = (fair["earnings_gap"].fillna(False).astype(bool)
                      & (fair["vol_source"] != "pre_earnings_flat"))
        fair = fair[~unrepaired]
    if fair.empty:
        return pd.DataFrame()

    merged = state.best_asks().merge(fair, on=["market_id", "ticker", "contract_type"],
                                     how="inner")
    if merged.empty:
        return pd.DataFrame()

    fee = state.params.fee_rate
    merged["cost"] = merged["ask"] + fee * np.minimum(merged["ask"], 1.0 - merged["ask"])
    is_yes = merged["outcome_index"] == YES
    k, atm = merged["fair_K"], merged["fair_atm"]
    # `np.fmin` ignores NaN and returns the other argument, so a market priced
    # off only one of the two vols would screen as though both agreed with it.
    # MODELS 5.1's bar is undefined there; require both.
    both = k.notna() & atm.notna()
    q_cons = np.where(both, np.where(is_yes, np.fmin(k, atm), np.fmax(k, atm)), np.nan)
    merged["fair"] = q_cons
    merged["win_prob"] = np.where(is_yes, q_cons, 1.0 - q_cons)
    # The value the bar is judged on: a YES is charged the risk premium.
    merged["premium"] = np.where(is_yes, yes_premium, 0.0)
    value = merged["win_prob"] - merged["premium"]
    merged["ev"] = value - merged["cost"]
    merged["roi"] = merged["ev"] / merged["cost"].replace(0.0, np.nan)

    dear = merged["ask"] > high_price
    ev_bar = np.where(dear, high_price_min_ev, min_ev)
    roi_bar = np.where(dear, high_price_min_roi, min_roi)
    merged["ev_bar"], merged["roi_bar"] = ev_bar, roi_bar
    # The limit the harness walks to: the worse of the two bars, so a deeper
    # level has to clear both, exactly as the top level did.
    by_ev = value - ev_bar
    by_roi = value / (1.0 + roi_bar)
    merged["limit_price"] = np.fmin(by_ev, by_roi)

    keep = (merged["win_prob"].notna() & (merged["ev"] >= ev_bar)
            & (merged["roi"] >= roi_bar) & (merged["cost"] < 1.0)
            & (merged["ask"] > 0))
    out = merged[keep].copy()
    out["fair_source"] = np.where(out["outcome_index"] == YES, "min(fair_K,fair_atm)",
                                  "max(fair_K,fair_atm)")
    return out.sort_values("roi", ascending=False).reset_index(drop=True)


# ------------------------------------------------------------ the fill engine


@dataclass
class Grant:
    """What the caps left of one order, and which cap did the cutting."""

    wanted_usd: float
    budget_usd: float
    blocker: str


def budget_for(order: Order, portfolio: Portfolio, params: Params,
               contract_type: str, ticker: str) -> Grant:
    """How many dollars an order may spend, and the binding constraint.

    Named so the report can say *why* a model did not deploy its bankroll.
    "Wanted $40k, filled $3k" is only interesting once it is split into the
    part the caps refused and the part the book could not supply.
    """
    market_cap = params.cap_yes_usd if order.outcome_index == YES else params.cap_no_usd
    wanted = min(order.max_usd, market_cap)
    room = {
        "market_cap": market_cap - portfolio.market_usd(order.market_id),
        "ticker_cap": params.ticker_cap_frac * params.bankroll
                      - portfolio.ticker_usd(ticker),
        "direction_cap": params.direction_cap_frac * params.bankroll
                         - portfolio.direction_usd(direction_of(contract_type,
                                                                order.outcome_index)),
        "cash": portfolio.cash,
        "order": order.max_usd,
    }
    blocker = min(room, key=lambda k: room[k])
    budget = max(0.0, min(min(room.values()), wanted))
    return Grant(wanted_usd=wanted, budget_usd=budget,
                 blocker="none" if budget >= wanted else blocker)


def walk_ladder(ladder: pd.DataFrame, order: Order, budget_usd: float,
                params: Params) -> list[dict]:
    """MODELS 4.2, level by level, in `depth_rank` order.

    Three stops, in this order at every level: the limit (`price + fee` at or
    above `limit_price` ends the walk - a deeper level is only ever worse), the
    `take_fraction` of that level's size, and the money left. The whole walk is
    discarded if it comes to less than one venue minimum, because a $0.40
    "fill" is an accounting entry that no exchange would have accepted.
    """
    taken: list[dict] = []
    left = budget_usd
    for level in ladder.sort_values("depth_rank").itertuples():
        price = float(level.price)
        if not (0.0 < price < 1.0):
            continue
        cost_per_share = all_in_cost(price, params.fee_rate)
        if cost_per_share >= order.limit_price:
            break
        available = params.take_fraction * float(level.size)
        affordable = left / cost_per_share
        shares = min(available, affordable)
        if shares <= 0:
            break
        spend = shares * cost_per_share
        taken.append({
            "depth_rank": int(level.depth_rank), "price": price, "shares": shares,
            "fee_usd": fee_per_share(price, params.fee_rate) * shares,
            "cost_usd": spend, "level_size": float(level.size),
        })
        left -= spend
        if left <= 1e-9:
            break
    if sum(t["cost_usd"] for t in taken) < params.min_fill_usd:
        return []
    return taken


def _market_meta(state: CycleState, market_id: str) -> dict | None:
    row = _row_for(state.fair, market_id)
    if row is not None:
        return {"ticker": row["ticker"], "contract_type": row["contract_type"],
                "barrier": float(row["barrier"]), "deadline": row["deadline_utc"],
                "period": row.get("period"),
                "fair_K": float(row.get("fair_K", float("nan")))}
    book = state.books[state.books["market_id"] == market_id]
    if book.empty:
        return None
    first = book.iloc[0]
    return {"ticker": first["ticker"], "contract_type": first["contract_type"],
            "barrier": float(first["barrier"]), "deadline": first["end_date"],
            "period": first.get("period"), "fair_K": float("nan")}


def execute(state: CycleState, orders: list[Order]) -> tuple[list[Fill], list[dict]]:
    """Fill a cycle's orders against that cycle's books. Returns fills and the
    per-order capacity record."""
    params, portfolio = state.params, state.portfolio
    fills: list[Fill] = []
    capacity: list[dict] = []
    for order in orders:
        meta = _market_meta(state, order.market_id)
        if meta is None:
            continue
        held = portfolio.held_direction(order.market_id)
        if held is not None and held != order.outcome_index:
            capacity.append(_capacity_row(state, order, meta, 0.0, 0.0, 0.0,
                                          "already_other_side"))
            continue
        existing = portfolio.open.get((order.market_id, order.outcome_index))
        if existing is not None:
            moved = abs(float(meta["fair_K"]) - existing.entry_fair_engine)
            allowed = (order.reenter and np.isfinite(moved)
                       and moved > params.reenter_fair_move)
            if not allowed:
                capacity.append(_capacity_row(state, order, meta, 0.0, 0.0, 0.0,
                                              "no_averaging_down"))
                continue
            # The reference moves with the re-entry. Leaving it at the first
            # entry's fair lets a fair oscillating 10c either side of it
            # re-enter every cycle, which is averaging down with extra steps -
            # measured on 2026-09-16 as five identical fills in one market.
            existing.entry_fair_engine = float(meta["fair_K"])

        grant = budget_for(order, portfolio, params, meta["contract_type"], meta["ticker"])
        ladder = state.ladder(order.market_id, order.outcome_index)
        taken = walk_ladder(ladder, order, grant.budget_usd, params) if grant.budget_usd > 0 else []
        spent = sum(t["cost_usd"] for t in taken)
        capacity.append(_capacity_row(state, order, meta, grant.wanted_usd,
                                      grant.budget_usd, spent,
                                      grant.blocker if grant.budget_usd < grant.wanted_usd
                                      else ("book" if spent < grant.budget_usd - 1e-6 else "none")))
        if not taken:
            continue

        position = portfolio.open.get((order.market_id, order.outcome_index))
        if position is None:
            position = Position(
                market_id=order.market_id, outcome_index=order.outcome_index,
                ticker=meta["ticker"], contract_type=meta["contract_type"],
                barrier=meta["barrier"], deadline=meta["deadline"],
                period=str(meta.get("period") or ""), entry_cycle_id=state.cycle_id,
                entry_at=state.now, entry_fair=float(order.fair),
                fair_source=order.fair_source,
                entry_fair_engine=float(meta["fair_K"]))
            portfolio.open[position.key] = position
        for t in taken:
            fill = Fill(cycle_id=state.cycle_id, filled_at=state.now,
                        market_id=order.market_id, outcome_index=order.outcome_index,
                        limit_price=order.limit_price, fair=order.fair,
                        fair_source=order.fair_source, reason=order.reason, **t)
            position.fills.append(fill)
            fills.append(fill)
            position.shares += fill.shares
            position.cost_usd += fill.cost_usd
            position.fee_usd += fill.fee_usd
            portfolio.cash -= fill.cost_usd
    return fills, capacity


def project(state: CycleState, order: Order) -> dict:
    """What `execute` would do with this order right now, without doing it.

    Models B and B-simple have to know the size of a position before they can
    decide whether they want it: a beta band and a quadrant balance are both
    statements about dollars, and the dollars come from the ladder, not from
    the cap. Projecting through the same `budget_for` and `walk_ladder` the
    harness uses is the only way the two can agree - a model with its own
    private idea of how deep the book is would refuse positions it could have
    had and accept ones it could not.
    """
    meta = _market_meta(state, order.market_id)
    if meta is None:
        return {"shares": 0.0, "cost_usd": 0.0, "budget_usd": 0.0,
                "blocker": "unknown_market"}
    # The lifetime rule first, exactly as `execute` applies it: a market
    # already held on either side fills nothing. The first version projected
    # such orders as full fills, and B-simple's quadrant tally then counted a
    # YES the harness went on to refuse - which is how its slack was breached
    # (behaviour checks, 2026-09-18).
    held = state.portfolio.held_direction(order.market_id)
    if held is not None and (held != order.outcome_index or not order.reenter):
        return {"shares": 0.0, "cost_usd": 0.0, "budget_usd": 0.0,
                "blocker": "already_other_side" if held != order.outcome_index
                else "no_averaging_down"}
    grant = budget_for(order, state.portfolio, state.params, meta["contract_type"],
                       meta["ticker"])
    taken = (walk_ladder(state.ladder(order.market_id, order.outcome_index), order,
                         grant.budget_usd, state.params) if grant.budget_usd > 0 else [])
    return {"shares": sum(t["shares"] for t in taken),
            "cost_usd": sum(t["cost_usd"] for t in taken),
            "budget_usd": grant.budget_usd, "blocker": grant.blocker}


def _capacity_row(state, order, meta, wanted, budget, filled, blocker) -> dict:
    return {"cycle_id": state.cycle_id, "market_id": order.market_id,
            "outcome_index": order.outcome_index, "ticker": meta["ticker"],
            "contract_type": meta["contract_type"], "wanted_usd": wanted,
            "budget_usd": budget, "filled_usd": filled, "blocker": blocker,
            "reason": order.reason}


# ------------------------------------------------------------- resolution


def _payout_per_share(result, outcome_prices, outcome_index: int) -> float | None:
    """1, 0 or 0.5 per share. `None` means the market has not really resolved.

    MODELS 4.3's 50-50 case is a null `result` with `outcome_prices` at
    0.5/0.5; a null `result` with anything else is a pending UMA dispute
    (MODELS section 6) and must not be booked as a loss.
    """
    if isinstance(result, str) and result in ("Yes", "No"):
        won = (result == "Yes") if outcome_index == YES else (result == "No")
        return 1.0 if won else 0.0
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


def cash_back_time(outcome_index: int, settled_at, deadline) -> pd.Timestamp:
    """When the money is actually free again.

    A YES is paid at the touch, which is `settled_at_utc`. A NO cannot be paid
    before the period is over however early the venue marks it, so its capital
    is locked to the deadline - MODELS 4.4's point about a 95c NO being real
    money committed for a month. Where the venue settles later than the
    deadline, the venue wins.
    """
    if outcome_index == YES:
        return settled_at
    if pd.isna(deadline):
        return settled_at
    return max(pd.Timestamp(deadline), pd.Timestamp(settled_at))


def settle(portfolio: Portfolio, resolutions: pd.DataFrame, now) -> list[Settlement]:
    """Close every open position whose market has settled by `now`."""
    if resolutions is None or resolutions.empty:
        return []
    by_market = resolutions.set_index("market_id")
    done: list[Settlement] = []
    for key in list(portfolio.open):
        market_id, outcome_index = key
        if market_id not in by_market.index:
            continue
        row = by_market.loc[market_id]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        payout = _payout_per_share(row.get("result"), row.get("outcome_prices"),
                                   outcome_index)
        if payout is None:
            continue
        position = portfolio.open.pop(key)
        settled_at = pd.Timestamp(row["settled_at_utc"])
        back_at = cash_back_time(outcome_index, settled_at, position.deadline)
        payout_usd = payout * position.shares
        capital_days = (position.cost_usd
                        * max((back_at - pd.Timestamp(position.entry_at)).total_seconds(), 0.0)
                        / 86400.0)
        done.append(Settlement(
            position=position, result=str(row.get("result")), settled_at=settled_at,
            cash_back_at=back_at, payout_per_share=payout, payout_usd=payout_usd,
            pnl_usd=payout_usd - position.cost_usd,
            capital_days=capital_days))
        portfolio.cash += payout_usd
        portfolio.settled.append(done[-1])
        portfolio.retired[key] = position
    return done


# ------------------------------------------------------------- the replay


class Loader:
    """Cycle-keyed access to the store, with the two frames a replay re-reads.

    Books are cached for a handful of cycles; the fair store is small enough to
    hold whole and slice.
    """

    def __init__(self, dates: list[str]):
        self.dates = list(dates)
        self._books: dict[str, pd.DataFrame] = {}
        self._fair_by_date: dict[str, pd.DataFrame] = {}
        self._cycles: list[str] | None = None

    def cycles(self) -> list[str]:
        if self._cycles is None:
            out: list[str] = []
            for date in self.dates:
                priced = set(self._fair(date)["cycle_id"].unique()) if len(self._fair(date)) else set()
                out += [c for c in data.session_cycles(date) if c in priced]
            self._cycles = sorted(out)
        return self._cycles

    def _fair(self, date: str) -> pd.DataFrame:
        if date not in self._fair_by_date:
            self._fair_by_date[date] = data.fair_values(date)
        return self._fair_by_date[date]

    def books(self, cycle_id: str) -> pd.DataFrame:
        if cycle_id not in self._books:
            if len(self._books) > 3:
                self._books.pop(next(iter(self._books)))
            self._books[cycle_id] = data.books(cycle_id)
        return self._books[cycle_id]

    def fair(self, cycle_id: str) -> pd.DataFrame:
        frame = self._fair(data.cycle_date(cycle_id))
        if frame.empty:
            return frame
        return frame[frame["cycle_id"] == cycle_id]


@dataclass
class Result:
    model: str
    params: Params
    run_id: str
    cycles: list[str]
    fills: list[Fill]
    capacity: pd.DataFrame
    marks: pd.DataFrame
    equity: pd.DataFrame
    settlements: list[Settlement]
    open_positions: list[Position]
    portfolio: Portfolio
    seconds: float
    settled_through: str

    @property
    def window(self) -> tuple[str, str]:
        if not self.cycles:
            return ("", "")
        return (data.cycle_date(self.cycles[0]), data.cycle_date(self.cycles[-1]))


def run(model, dates: list[str] | None = None, params: Params | None = None,
        run_id: str | None = None, loader: Loader | None = None,
        settle_through: str | None = None) -> Result:
    """Replay every priced session cycle of `dates` for one model.

    The order inside a cycle is: settle whatever has resolved by now, let the model propose,
    fill, then mark. Settling before proposing is what lets a model spend cash
    that came back this morning; marking after filling is what puts the new
    position into the exposure series it is part of.
    """
    import time

    started = time.time()
    params = params or Params()
    loader = loader or Loader(dates or data.fair_dates())
    cycles = loader.cycles()
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    portfolio = Portfolio(params)

    all_fills: list[Fill] = []
    capacity_rows: list[dict] = []
    mark_rows: list[dict] = []
    equity_rows: list[dict] = []

    for cycle_id in cycles:
        books = loader.books(cycle_id)
        fair = loader.fair(cycle_id)
        now = _cycle_time(books, fair, cycle_id)
        resolutions = data.resolutions_upto(now)
        settle(portfolio, resolutions, now)

        tickers = sorted(set(fair["ticker"].dropna())) if len(fair) else []
        betas = factors.beta_table(tickers, now)
        state = CycleState(cycle_id=cycle_id, now=now, books=books, fair=fair,
                           resolutions=resolutions, portfolio=portfolio, betas=betas,
                           spy_intraday=factors.spy_intraday_return(now),
                           params=params, loader=loader)
        orders = list(model.propose(state) or [])
        fills, capacity = execute(state, orders)
        all_fills += fills
        capacity_rows += capacity
        cycle_marks = _marks(state, run_id, model.name, betas)
        mark_rows += cycle_marks
        equity_rows.append(_equity(state, cycle_marks))

    # The run's decisions are over; what has settled since is not lookahead,
    # it is the outcome. MODELS 4.3: headline numbers are resolved-only.
    through = settle_through or _latest_settlement()
    settle(portfolio, data.resolutions_upto(through), through)

    return Result(model=model.name, params=params, run_id=run_id, cycles=cycles,
                  fills=all_fills, capacity=pd.DataFrame(capacity_rows),
                  marks=pd.DataFrame(mark_rows), equity=pd.DataFrame(equity_rows),
                  settlements=list(portfolio.settled),
                  open_positions=list(portfolio.open.values()), portfolio=portfolio,
                  seconds=time.time() - started, settled_through=str(through))


def _cycle_time(books: pd.DataFrame, fair: pd.DataFrame, cycle_id: str) -> pd.Timestamp:
    """The cycle's own clock: the newest fetch in it, else the id's stamp.

    `fetched_at_utc`, not `book_timestamp_utc`: the latter is the venue's last
    change to a book and can be days old, so using it would let a model with a
    fossil book in its cycle see resolutions from before that fossil only.
    """
    if len(books) and books["fetched_at_utc"].notna().any():
        return pd.Timestamp(books["fetched_at_utc"].max())
    return pd.Timestamp(data.cycle_start(cycle_id))


def _latest_settlement() -> str:
    """The newest settlement on disk: how far a finished run may be marked."""
    frame = data.resolutions_upto("2100-01-01")
    if frame.empty:
        return "2100-01-01"
    return str(pd.Timestamp(frame["settled_at_utc"].max()))


def _equity(state: CycleState, cycle_marks: list[dict]) -> dict:
    """One row per cycle: what the book was worth, and which way it leaned.

    Marked at the model's own fair and at the last bid, because MODELS 4.3
    wants both and they are far apart in a book this thin - the bid mark is
    what a forced seller would get, the fair mark is what the model believes.
    """
    fair_mark = float(np.nansum([m["mark_fair_usd"] for m in cycle_marks]))
    bid_mark = float(np.nansum([m["mark_bid_usd"] for m in cycle_marks]))
    cost = float(np.nansum([m["cost_usd"] for m in cycle_marks]))
    bdd = float(np.nansum([m["beta_dollar_delta"] for m in cycle_marks]))
    return {"cycle_id": state.cycle_id, "at_utc": state.now,
            "date": data.cycle_date(state.cycle_id), "cash": state.portfolio.cash,
            "n_open": len(cycle_marks), "open_cost_usd": cost,
            "mark_fair_usd": fair_mark, "mark_bid_usd": bid_mark,
            "equity_fair_usd": state.portfolio.cash + fair_mark,
            "equity_bid_usd": state.portfolio.cash + bid_mark,
            "beta_dollar_delta": bdd,
            "beta_dollar_delta_frac": bdd / state.params.bankroll}


def beta_dollar_delta(position: Position, delta: float, spot: float,
                      beta: float) -> float:
    """MODELS 5.2: a position's beta-weighted dollar delta. `delta` is the
    YES-side number, so a NO carries the opposite sign."""
    if not (np.isfinite(delta) and np.isfinite(spot)):
        return float("nan")
    signed = delta if position.outcome_index == YES else -delta
    return signed * spot * position.shares * beta


def _marks(state: CycleState, run_id: str, model: str, betas: dict) -> list[dict]:
    bids = state.best_bids()
    lookup = {(r.market_id, r.outcome_index): r.bid for r in bids.itertuples()}
    rows = []
    for position in state.portfolio.open.values():
        fair_row = _row_for(state.fair, position.market_id)
        fair = model_fair(fair_row, position.fair_source)
        delta = float(fair_row["delta"]) if fair_row is not None else float("nan")
        spot = float(fair_row["spot"]) if fair_row is not None else float("nan")
        bid = float(lookup.get(position.key, float("nan")))
        beta = float(betas.get(position.ticker, 1.0))
        rows.append({
            "run_id": run_id, "model": model, "cycle_id": state.cycle_id,
            "marked_at_utc": state.now.isoformat(), "market_id": position.market_id,
            "outcome_index": position.outcome_index, "ticker": position.ticker,
            "contract_type": position.contract_type, "shares": position.shares,
            "cost_usd": position.cost_usd, "fair": fair,
            "mark_fair_usd": position.win_probability(fair) * position.shares,
            "bid": bid, "mark_bid_usd": bid * position.shares,
            "delta": delta, "spot": spot, "beta": beta,
            "beta_dollar_delta": beta_dollar_delta(position, delta, spot, beta),
        })
    return rows


# ------------------------------------------------------------- the control


class RandomControl:
    """MODELS 4.5's control: the same sizing rules, no signal at all.

    It buys uniformly-chosen tokens at whatever the top of the book asks, up to
    two cents of slippage, under the same caps as everybody else. If it makes
    money, the window drifted and no model in this repository has demonstrated
    an edge - which is the only reason to run it.

    `orders_per_cycle` is set by the caller to the activity level of the model
    being controlled for; a control that trades ten times less than the model
    is a control for a different experiment.
    """

    name = "random_control"

    def __init__(self, orders_per_cycle: int = 12, seed: int = 20260918,
                 slip: float = 0.02, exclude_deep_otm: bool = True):
        self.orders_per_cycle = orders_per_cycle
        self.slip = slip
        self.exclude_deep_otm = exclude_deep_otm
        self._rng = random.Random(seed)

    def propose(self, state: CycleState) -> list[Order]:
        fair = state.fair
        if fair.empty:
            return []
        universe = fair[~fair["touch_pending"].fillna(False).astype(bool)]
        if self.exclude_deep_otm:
            universe = universe[~universe["deep_otm"].fillna(False).astype(bool)]
        universe = universe[~universe["no_chain"].fillna(False).astype(bool)]
        if universe.empty:
            return []
        best = state.best_asks()
        pool = best[best["market_id"].isin(set(universe["market_id"]))]
        pool = pool[(pool["ask"] > 0.005) & (pool["ask"] < 0.995)]
        if pool.empty:
            return []
        rows = pool.to_dict("records")
        picks = self._rng.sample(rows, min(self.orders_per_cycle, len(rows)))
        cap = state.params
        return [Order(market_id=r["market_id"], outcome_index=int(r["outcome_index"]),
                      max_usd=cap.cap_yes_usd if int(r["outcome_index"]) == YES
                      else cap.cap_no_usd,
                      limit_price=all_in_cost(float(r["ask"]), cap.fee_rate) + self.slip,
                      reason="random", fair=float("nan"), fair_source="none")
                for r in picks]
