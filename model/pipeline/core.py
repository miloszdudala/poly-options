"""The pipeline's plumbing: the variant registry, the cycle, and the runner.

A **step** is one decision (which markets, which vol, which probability, ...).
A **variant** is one way of making it, registered under a name:

    @variant("vol", "atm_first_expiry_after")
    def atm(markets, cycle, params): ...

The config (`configs/*.yaml`) picks one variant per step and gives it its
parameters. A pricing step takes the cycle's markets as a frame and returns it
with columns added; it never deletes a row. A market a step rules out gets its
reason in `gate` (`drop()`), so every market of every cycle can be traced to
the step that stopped it.
"""
from __future__ import annotations

import functools

import pandas as pd

import store
from sessions import nyse_sessions

VARIANTS: dict[str, dict] = {}


def variant(step: str, name: str):
    """Register `fn` as the variant `name` of `step`."""
    def wrap(fn):
        VARIANTS.setdefault(step, {})[name] = fn
        return fn
    return wrap


def get(step: str, name: str):
    try:
        return VARIANTS[step][name]
    except KeyError:
        known = ", ".join(sorted(VARIANTS.get(step, {}))) or "none"
        raise KeyError(f"no variant {name!r} for step {step!r}; known: {known}") from None


def drop(markets: pd.DataFrame, mask, reason: str) -> pd.DataFrame:
    """Rule out the markets in `mask` that are still alive, for `reason`."""
    mask = pd.Series(mask, index=markets.index).fillna(False).astype(bool)
    markets.loc[mask & markets["gate"].isna(), "gate"] = reason
    return markets


def alive(markets: pd.DataFrame) -> pd.Series:
    return markets["gate"].isna()


class NotReady(Exception):
    """The cycle cannot be priced yet (e.g. its bars have not arrived)."""


@functools.lru_cache(maxsize=1)
def sessions() -> pd.DataFrame:
    return nyse_sessions()


class Cycle:
    """Everything one cycle offers, read lazily from the store and memoised."""

    def __init__(self, cycle_id: str, contract_types: tuple[str, ...], venue=None):
        if venue is None:
            from pipeline.venues import Polymarket
            venue = Polymarket()
        self.venue = venue
        self.id = cycle_id
        self.date = store.cycle_date(cycle_id)
        self.contract_types = tuple(contract_types)
        self._memo: dict = {}

    def memo(self, key, compute):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    @property
    def books(self) -> pd.DataFrame:
        """Every level of every book of the cycle's contract types."""
        return self.memo("books", lambda: self.venue.books(self.id, self.contract_types))

    @property
    def asks(self) -> pd.DataFrame:
        books = self.books
        return books[books["side"] == "ask"] if len(books) else books

    @property
    def now(self) -> pd.Timestamp:
        """The cycle's clock: the newest fetch in its books, else the id's stamp.
        `fetched_at_utc`, not `book_timestamp_utc`: the latter is the venue's last
        change to a book and can be days old."""
        books = self.books
        if len(books) and books["fetched_at_utc"].notna().any():
            return pd.Timestamp(books["fetched_at_utc"].max())
        return pd.Timestamp(store.cycle_start(self.id))

    def chain(self, symbols: tuple[str, ...]) -> pd.DataFrame:
        return self.memo(("chain", symbols), lambda: store.option_chain(self.id, symbols))

    def quote_times(self) -> pd.Series:
        return self.memo("quote_times", lambda: store.quote_times(self.id))

    def cboe_spot(self) -> pd.Series:
        return self.memo("cboe_spot", lambda: store.cboe_spot(self.id)
                         .set_index("symbol")["last_price"])

    def rate(self, when) -> float:
        return self.memo(("rate", str(when)[:10]), lambda: store.risk_free_rate(when))


PRICING_STEPS = ("markets", "inputs", "vol", "prob", "gates")


def price(cycle: Cycle, config: dict) -> pd.DataFrame:
    """Run the pricing steps on one cycle: every market of the universe, with
    its fair (the probability YES wins) or the `gate` that ruled it out.

    Raises `NotReady` when the cycle cannot be priced yet.
    """
    markets = None
    for step in PRICING_STEPS:
        params = dict(config[step])
        fn = get(step, params.pop("use"))
        markets = fn(cycle, params) if step == "markets" else fn(markets, cycle, params)
        if markets.empty:
            break
    return markets


def empty_markets(columns=()) -> pd.DataFrame:
    base = ["market_id", "ticker", "contract_type", "level", "deadline", "period",
            "yes_direction", "yes_vol", "fair", "gate"]
    return pd.DataFrame(columns=base + [c for c in columns if c not in base])
