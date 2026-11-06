"""Step 0 - venue: where the books, the cycles and the results come from.

Not a decision about a bet, but every step after it reads through it.

| variant | cycles | books | results |
|---|---|---|---|
| `polymarket` | session cycles with books and a clean run (`store.session_cycles`) | `poly_books` | `resolutions`, labels read against the market's outcomes |
| `kalshi` (`series`) | in-session cycles with books of the series | `kalshi_books`: bids on both sides, turned into the asks a taker buys from | `kalshi_resolutions` |
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import store
from pipeline.core import variant
from pipeline.settle import resolutions as polymarket_resolutions


@variant("venue", "polymarket")
class Polymarket:
    def cycles(self, dates: list[str]) -> list[str]:
        return [c for date in dates for c in store.session_cycles(date)]

    def books(self, cycle_id: str, contract_types: tuple[str, ...]) -> pd.DataFrame:
        return store.books(cycle_id, contract_types)

    def resolutions(self, upto) -> pd.DataFrame:
        return polymarket_resolutions(upto)


@variant("venue", "kalshi")
class Kalshi:
    def __init__(self, series: list[str]):
        self.series = tuple(series)

    def _sql(self) -> str:
        return ", ".join(f"'{s}'" for s in self.series)

    def cycles(self, dates: list[str]) -> list[str]:
        if not dates:
            return []
        quoted = ", ".join(f"'{d}'" for d in dates)
        frame = store._q(f"""
            select distinct cycle_id from {store._src('kalshi_books')}
            where date in ({quoted}) and series_ticker in ({self._sql()})
        """)
        return sorted(c for c in frame["cycle_id"] if store.in_session(c))

    def books(self, cycle_id: str, contract_types=()) -> pd.DataFrame:
        bids = store._q(f"""
            select cycle_id, fetched_at_utc, ticker, side, depth_rank, price_prob, size
            from {store._src('kalshi_books')}
            where cycle_id = '{cycle_id}' and series_ticker in ({self._sql()})
        """)
        return kalshi_asks(bids)

    def resolutions(self, upto) -> pd.DataFrame:
        """Each market's result, stamped when Kalshi settled it."""
        stamp = pd.Timestamp(upto)
        stamp = stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")
        frame = store._q(f"""
            select ticker market_id, lower(any_value(result)) result,
                   any_value(settled_at_utc) settled_at_utc
            from {store._src('kalshi_resolutions')}
            where series_ticker in ({self._sql()}) and lower(result) in ('yes', 'no')
            group by ticker
        """)
        frame["settled_at_utc"] = pd.to_datetime(frame["settled_at_utc"], format="ISO8601",
                                                 utc=True, errors="coerce")
        frame["result"] = frame["result"].map({"yes": "Yes", "no": "No"})
        frame["outcome_prices"] = None
        return frame[frame["settled_at_utc"] <= stamp]


def kalshi_asks(bids: pd.DataFrame) -> pd.DataFrame:
    """Kalshi's two bid ladders as the two ask ladders a taker buys from: outcome
    0 (YES) from the NO bids at 1 - price, outcome 1 (NO) from the YES bids.
    Best bid first is best ask first, so `depth_rank` carries over."""
    if bids.empty:
        return pd.DataFrame(columns=["cycle_id", "fetched_at_utc", "market_id", "outcome_index",
                                     "side", "depth_rank", "price", "size"])
    out = pd.DataFrame({
        "cycle_id": bids["cycle_id"],
        "fetched_at_utc": pd.to_datetime(bids["fetched_at_utc"], format="ISO8601", utc=True,
                                         errors="coerce"),
        "market_id": bids["ticker"].astype(str),
        "outcome_index": np.where(bids["side"] == "no", 0, 1),
        "side": "ask",
        "depth_rank": bids["depth_rank"].astype(int),
        "price": 1.0 - bids["price_prob"].astype(float),
        "size": bids["size"].astype(float),
    })
    return out.sort_values(["market_id", "outcome_index", "depth_rank"]).reset_index(drop=True)
