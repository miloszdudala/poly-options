"""The type format: which markets belong to a type, how to price one, its fee.

A type is one file in `model/types/` with one subclass of `Type` (or of a
venue base below) that sets `name`. The harness finds it
(`run.discover_types`); adding a type is adding a file. Files whose name starts
with `_` are helpers for the type files, not types.

What the harness asks of a type, per cycle:

* `cycles(dates)` - the cycles to replay, in order;
* `books(cycle_id)` - every level of every book of the type, with
  `market_id`, `outcome_index` (0 YES, 1 NO), `side` ('ask' / 'bid'),
  `depth_rank`, `price`, `size`, `fetched_at_utc`; only asks are bought;
* `priced(cycle_id)` - one row per market that may be traded this cycle:
  `market_id`, `ticker`, `deadline` (UTC), `fair` (the probability YES wins),
  `yes_direction` ('up', 'down' or None: which way YES leans; NO leans the
  other way), `yes_vol` ('wild' or None: NO is then 'calm'), and for the
  ledger `contract_type`, `level` and `period`;
* `fee(price, market_id, now)` - the venue's fee per share at that price, on
  that market, at that moment;
* `resolutions(upto)` - every market that had settled by `upto`, with
  `market_id`, `result` ('Yes' / 'No'), `outcome_prices`, `settled_at_utc`;
* `skipped` - market counts left out, by reason, for the report;
* `note` - a caveat the reports print beside the type's numbers.

`period` ('daily', 'weekly' or 'monthly') sets the budget period of the
`budget` account and the period the report groups by.
"""
from __future__ import annotations

import pandas as pd

import data

YES_INDEX, NO_INDEX = 0, 1


class Type:
    name = ""
    period = "weekly"
    note = ""

    def __init__(self):
        self.skipped: dict[str, int] = {}

    def skip(self, reason: str, n: int = 1) -> None:
        if n:
            self.skipped[reason] = self.skipped.get(reason, 0) + int(n)

    def cycles(self, dates: list[str]) -> list[str]:
        raise NotImplementedError

    def books(self, cycle_id: str) -> pd.DataFrame:
        raise NotImplementedError

    def priced(self, cycle_id: str) -> pd.DataFrame:
        raise NotImplementedError

    def fee(self, price: float, market_id: str | None = None, now=None) -> float:
        raise NotImplementedError

    def resolutions(self, upto) -> pd.DataFrame:
        raise NotImplementedError


class Polymarket(Type):
    """Books, fee and resolutions as Polymarket's. `contract_types` picks the books."""

    contract_types: tuple[str, ...] = ()
    #: Polymarket's finance rate, for a market with no recorded schedule.
    #: https://docs.polymarket.com/trading/fees
    fee_rate = 0.04

    def books(self, cycle_id: str) -> pd.DataFrame:
        return data.books(cycle_id, self.contract_types)

    def fee(self, price: float, market_id: str | None = None, now=None) -> float:
        """`rate * (p * (1 - p)) ** exponent`, with the market's own schedule at
        `now` (`poly_markets.fee_schedule`), charged once, on the price paid."""
        known = data.poly_fee_at(market_id, now) if market_id is not None else None
        rate, exponent = known if known is not None else (self.fee_rate, 1.0)
        return rate * (price * (1.0 - price)) ** exponent

    def resolutions(self, upto) -> pd.DataFrame:
        return data.resolutions_upto(upto)


#: Underlyings that are not stocks (commodities, FX, metals and their ETFs).
NOT_STOCKS = frozenset({"WTI", "XAUUSD", "XAGUSD", "DXY", "GLD", "SLV", "USO", "UUP"})


class PolymarketHit(Polymarket):
    """Polymarket "will X hit $B by the deadline" markets on stocks, priced by
    the engine (`engine.price_cycle`, one-touch with ATM vol) and read from the
    fair store. A subclass sets `name` and `period`."""

    contract_types = ("hit_up", "hit_down")

    def __init__(self):
        super().__init__()
        self._fair_by_date: dict[str, pd.DataFrame] = {}

    def _fair(self, date: str) -> pd.DataFrame:
        if date not in self._fair_by_date:
            self._fair_by_date[date] = data.fair_values(date)
        return self._fair_by_date[date]

    def cycles(self, dates: list[str]) -> list[str]:
        out = []
        for date in dates:
            fair = self._fair(date)
            if len(fair):
                priced = set(fair["cycle_id"])
                out += [c for c in data.session_cycles(date) if c in priced]
        return sorted(out)

    def mine(self, fair: pd.DataFrame) -> pd.DataFrame:
        """The markets of this type: hit markets of `period` on stocks."""
        return fair[(fair["period"] == self.period) & ~fair["ticker"].isin(NOT_STOCKS)]

    def priced(self, cycle_id: str) -> pd.DataFrame:
        fair = self._fair(data.cycle_date(cycle_id))
        if fair.empty:
            return pd.DataFrame()
        fair = self.mine(fair[fair["cycle_id"] == cycle_id])
        flag = lambda col: fair[col].fillna(False).astype(bool)  # noqa: E731
        # A market already touched is worth 1 by arithmetic; a deep-OTM or
        # chainless one has no option fair worth trading (MODELS 3.4). An
        # earnings report between the deadline and the expiry inflates the vol
        # the barrier never sees.
        drop = flag("touch_pending") | flag("deep_otm") | flag("no_chain") | flag("earnings_gap")
        # The chain is normally ~13 minutes older than the book. When a slow
        # tick reverses that (2026-09-16 13:40Z: prints 10 minutes newer than
        # the book), the fair knows something the book did not, and a fill
        # against that book is not one a taker could have made.
        drop |= fair["quote_age_s"] < 0
        fair = fair[~drop]
        up = fair["contract_type"] == "hit_up"
        return pd.DataFrame({
            "market_id": fair["market_id"].astype(str), "ticker": fair["ticker"],
            "deadline": fair["deadline_utc"], "fair": fair["fair"].astype(float),
            "yes_direction": up.map({True: "up", False: "down"}), "yes_vol": "wild",
            "contract_type": fair["contract_type"], "level": fair["barrier"].astype(float),
            "period": fair["period"],
        }).reset_index(drop=True)
