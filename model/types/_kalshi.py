"""Shared by the Kalshi index types: books, terms, results and the fee.

Kalshi's S&P 500 and Nasdaq-100 "above X / in range at time T" markets are
priced off the SPX / NDX call spread (`_chain.prob_above`) at the expiry whose
16:00 ET close is the market's observation time (`close_time`); no exact
expiry, no price (skipped and counted, as for the Polymarket chain types).

Kalshi books are collected hourly and the option chain is ~15 minutes delayed,
so a market observed at an hour other than 16:00 ET is skipped by the expiry
rule, and the fair is up to 15 minutes older than the book.

Kalshi lists resting bids on both sides. Buying YES takes the best NO bids
(YES ask = 1 - NO bid, at the NO bid's size); buying NO takes the YES bids.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import bet_types
import data
from _chain import (SKIP_NO_CHAIN, SKIP_NO_EXPIRY, SKIP_NO_STRIKES, SKIP_NO_TERMS,
                    prob_above)
from engine import STALE_CHAIN_S, expiry_closes
from sessions import nyse_sessions

SKIP_STRIKE_TYPE = "strike type not priced"
#: Kalshi taker fee per contract: 0.07 * multiplier * P * (1 - P), the fee rounded
#: up to $0.000001 (docs.kalshi.com/getting_started/fee_rounding). These series
#: are `quadratic` with `fee_multiplier` 1, set 2026-07-03, per Kalshi's API
#: (/trade-api/v2/series/fee_changes, read 2026-11-04). The 0.035 half rate in
#: older guides and the February 2026 PDF is no longer what the API lists.
INDEX_FEE_RATE = 0.07


class KalshiIndex(bet_types.Type):
    """A subclass sets `name`, `period`, `series` and `underlying`."""

    series: tuple[str, ...] = ()
    underlying = ""
    note = ("Kalshi books are collected hourly and option prices are 15 minutes "
            "delayed; that matters most for markets observed at an hour.")

    def __init__(self):
        super().__init__()
        self._terms: pd.DataFrame | None = None
        self._sessions = nyse_sessions()
        self._skipped_markets: set[tuple[str, str]] = set()
        self._market = ""

    def skip(self, reason: str, n: int = 1) -> None:
        if (reason, self._market) not in self._skipped_markets:
            self._skipped_markets.add((reason, self._market))
            super().skip(reason)

    def fee(self, price: float, market_id: str | None = None, now=None) -> float:
        return INDEX_FEE_RATE * price * (1.0 - price)

    def _series_sql(self) -> str:
        return ", ".join(f"'{s}'" for s in self.series)

    def terms(self) -> pd.DataFrame:
        if self._terms is None:
            frame = data._q(f"""
                select ticker, series_ticker, strike_type, floor_strike, cap_strike, close_time
                from (select *, row_number() over (partition by ticker
                                                   order by fetched_at_utc desc) rn
                      from {data._src('kalshi_markets')}
                      where series_ticker in ({self._series_sql()}))
                where rn = 1
            """)
            frame["close_time"] = pd.to_datetime(frame["close_time"], format="ISO8601",
                                                 utc=True, errors="coerce")
            self._terms = frame.set_index("ticker")
        return self._terms

    def cycles(self, dates: list[str]) -> list[str]:
        quoted = ", ".join(f"'{d}'" for d in dates)
        if not quoted:
            return []
        frame = data._q(f"""
            select distinct cycle_id from {data._src('kalshi_books')}
            where date in ({quoted}) and series_ticker in ({self._series_sql()})
        """)
        return sorted(c for c in frame["cycle_id"] if data.in_session(c))

    def books(self, cycle_id: str) -> pd.DataFrame:
        bids = data._q(f"""
            select cycle_id, fetched_at_utc, ticker, side, depth_rank, price_prob, size
            from {data._src('kalshi_books')}
            where cycle_id = '{cycle_id}' and series_ticker in ({self._series_sql()})
        """)
        return kalshi_asks(bids)

    def resolutions(self, upto) -> pd.DataFrame:
        """Each market's result, stamped when the collector first saw it."""
        stamp = pd.Timestamp(upto)
        stamp = stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")
        frame = data._q(f"""
            select ticker market_id, lower(arg_min(result, fetched_at_utc)) result,
                   min(fetched_at_utc) settled_at_utc
            from {data._src('kalshi_markets')}
            where series_ticker in ({self._series_sql()}) and lower(result) in ('yes', 'no')
            group by ticker
        """)
        frame["settled_at_utc"] = pd.to_datetime(frame["settled_at_utc"], format="ISO8601",
                                                 utc=True, errors="coerce")
        frame["result"] = frame["result"].map({"yes": "Yes", "no": "No"})
        frame["outcome_prices"] = None
        return frame[frame["settled_at_utc"] <= stamp]

    def priced(self, cycle_id: str) -> pd.DataFrame:
        asks = self.books(cycle_id)
        if asks.empty:
            return pd.DataFrame()
        terms = self.terms()
        chain = data.option_chain(cycle_id, (self.underlying,))
        calls = chain[chain["option_type"] == "call"]
        q_time = data.quote_times(cycle_id).get(self.underlying, pd.NaT)
        fetched = pd.Timestamp(asks["fetched_at_utc"].max())
        rate = data.risk_free_rate(fetched)
        closes = expiry_closes(sorted(calls["expiry"].dropna().unique()), self._sessions) \
            if len(calls) else {}
        rows = []
        for ticker in sorted(asks["market_id"].unique()):
            self._market = ticker
            if ticker not in terms.index:
                self.skip(SKIP_NO_TERMS)
                continue
            term = terms.loc[ticker]
            if calls.empty or pd.isna(q_time) or (fetched - q_time).total_seconds() > STALE_CHAIN_S:
                self.skip(SKIP_NO_CHAIN)
                continue
            at = term["close_time"]
            expiry = next((e for e, c in closes.items()
                           if pd.notna(c) and pd.notna(at) and c.floor("min") == at.floor("min")),
                          None)
            if expiry is None:
                self.skip(SKIP_NO_EXPIRY)
                continue
            years = (at - q_time).total_seconds() / (365.0 * 86400.0)
            mine = calls[calls["expiry"] == expiry]
            fair, direction = fair_yes(term, lambda k: prob_above(mine, float(k), rate, years))
            if fair is None:
                self.skip(SKIP_STRIKE_TYPE)
                continue
            if not np.isfinite(fair):
                self.skip(SKIP_NO_STRIKES)
                continue
            level = term["floor_strike"] if pd.notna(term["floor_strike"]) else term["cap_strike"]
            rows.append({"market_id": ticker, "ticker": self.underlying, "deadline": at,
                         "fair": float(np.clip(fair, 0.0, 1.0)), "yes_direction": direction,
                         "yes_vol": None, "contract_type": str(term["strike_type"]),
                         "level": float(level), "period": self.period})
        return pd.DataFrame(rows)


def fair_yes(term, above) -> tuple[float | None, str | None]:
    """P(YES) and the direction YES leans, by Kalshi `strike_type`.

    `greater(_or_equal)`: above `floor_strike`; `less(_or_equal)`: below
    `cap_strike`; `between`: from `floor_strike` to `cap_strike`. Anything else
    returns (None, None).
    """
    kind = str(term.get("strike_type") or "")
    if kind in ("greater", "greater_or_equal"):
        return above(term["floor_strike"]), "up"
    if kind in ("less", "less_or_equal"):
        return 1.0 - above(term["cap_strike"]), "down"
    if kind == "between":
        return above(term["floor_strike"]) - above(term["cap_strike"]), None
    return None, None


def kalshi_asks(bids: pd.DataFrame) -> pd.DataFrame:
    """Kalshi's two bid ladders as the two ask ladders a taker buys from.

    Outcome 0 (YES) is bought from the NO bids at 1 - price; outcome 1 (NO)
    from the YES bids. Best bid first is best ask first, so `depth_rank`
    carries over.
    """
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
