"""Shared by the types priced directly from the option chain (no touch formula).

P(the underlying finishes above K) is read off the call spread around K at the
expiry whose 16:00 ET close *is* the market's deadline:

    P(S_T > K) = e^{rT} (C(K1) - C(K2)) / (K2 - K1)

with K1 the nearest listed strike below K and K2 the nearest above (a strike at
K itself is skipped, so the spread is centred on K whenever the strikes are
evenly spaced). The call spread is the textbook digital: on a flat-vol chain it
is N(d2). If no expiry closes exactly at the deadline, the market is skipped
and counted, never interpolated.

The chain is Cboe's, about 15 minutes older than the book; a chain older than
`engine.STALE_CHAIN_S` is treated as absent. Markets on non-stocks
(`bet_types.NOT_STOCKS`) are left to their own types.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import bet_types
import data
from engine import STALE_CHAIN_S, expiry_closes
from sessions import deadline_close, nyse_sessions

SKIP_NO_EXPIRY = "no expiry at the deadline"
SKIP_NO_STRIKES = "no call strikes either side of the level"
SKIP_NO_CHAIN = "no fresh option chain"
SKIP_NO_TERMS = "no market terms"
SKIP_NO_OPEN = "no opening price yet"


def prob_above(calls: pd.DataFrame, level: float, rate: float, years: float) -> float:
    """P(S_T > level) from the call spread around `level`.

    `calls` holds one expiry's calls: `strike` and `mid`. NaN when there is no
    listed strike on one side of the level.
    """
    strikes = calls.dropna(subset=["mid"]).groupby("strike")["mid"].mean().sort_index()
    below = strikes[strikes.index < level]
    above = strikes[strikes.index > level]
    if below.empty or above.empty:
        return float("nan")
    k1, c1 = float(below.index[-1]), float(below.iloc[-1])
    k2, c2 = float(above.index[0]), float(above.iloc[0])
    p = math.exp(rate * max(years, 0.0)) * (c1 - c2) / (k2 - k1)
    return float(np.clip(p, 0.0, 1.0))


class ChainPriced(bet_types.Polymarket):
    """A Polymarket type of one contract type and period, priced off calls.

    A subclass sets `name`, `contract_type`, `period`, and writes
    `fair_yes(probability_above, market)`: the probability that outcome 0
    wins, given a function that prices "finishes above K" for this market.
    """

    contract_type = ""

    def __init__(self):
        super().__init__()
        self.contract_types = (self.contract_type,)
        self._terms: pd.DataFrame | None = None
        self._books: tuple[str, pd.DataFrame] | None = None
        self._sessions = nyse_sessions()
        self._skipped_markets: set[tuple[str, str]] = set()
        self._market = ""

    def skip(self, reason: str, n: int = 1) -> None:
        """Counted once per market and reason, however many cycles skip it."""
        if (reason, self._market) not in self._skipped_markets:
            self._skipped_markets.add((reason, self._market))
            super().skip(reason)

    # -- what belongs to the type --------------------------------------------

    def terms(self) -> pd.DataFrame:
        """The newest `poly_markets` row per market of this contract type."""
        if self._terms is None:
            self._terms = data._q(f"""
                select market_id, ticker, contract_type, strike, range_low, range_high,
                       direction, period, end_date, outcome_0, outcome_1
                from (select *, row_number() over (partition by market_id
                                                   order by fetched_at_utc desc) rn
                      from {data._src('poly_markets')}
                      where contract_type = '{self.contract_type}')
                where rn = 1
            """).set_index("market_id")
        return self._terms

    def cycles(self, dates: list[str]) -> list[str]:
        return sorted(c for date in dates for c in data.session_cycles(date))

    def books(self, cycle_id: str) -> pd.DataFrame:
        if self._books is None or self._books[0] != cycle_id:
            self._books = (cycle_id, data.books(cycle_id, self.contract_types))
        return self._books[1]

    def resolutions(self, upto) -> pd.DataFrame:
        """Venue results, with the winning label turned into Yes (outcome 0
        won) or No (outcome 1 won), so an Up/Down market settles like any other."""
        frame = data.resolutions_upto(upto)
        if frame.empty:
            return frame
        terms = self.terms()
        first = frame["market_id"].astype(str).map(terms["outcome_0"]) \
            if len(terms) else pd.Series(index=frame.index, dtype=object)
        second = frame["market_id"].astype(str).map(terms["outcome_1"]) \
            if len(terms) else pd.Series(index=frame.index, dtype=object)
        result = frame["result"].astype(object)
        result = np.where(result == first, "Yes", np.where(result == second, "No", result))
        return frame.assign(result=result)

    # -- pricing ---------------------------------------------------------------

    def fair_yes(self, prob_above_at, market) -> float:
        raise NotImplementedError

    def yes_direction(self, market) -> str | None:
        raise NotImplementedError

    def priced(self, cycle_id: str) -> pd.DataFrame:
        books = self.books(cycle_id)
        if books.empty:
            return pd.DataFrame()
        markets = (books.groupby("market_id")
                   .agg(ticker=("ticker", "first"), end_date=("end_date", "first"),
                        period=("period", "first"), fetched_at=("fetched_at_utc", "max"))
                   .reset_index())
        markets["market_id"] = markets["market_id"].astype(str)
        markets = markets[(markets["period"] == self.period)
                          & ~markets["ticker"].isin(bet_types.NOT_STOCKS)]
        if markets.empty:
            return pd.DataFrame()
        terms = self.terms()
        chain = data.option_chain(cycle_id, tuple(sorted(markets["ticker"].dropna().unique())))
        quote_time = data.quote_times(cycle_id)
        rate = data.risk_free_rate(markets["fetched_at"].max())
        deadlines = pd.to_datetime(deadline_close(
            markets["end_date"].values.astype("datetime64[ns]"), self._sessions), utc=True)
        rows = []
        for market, deadline in zip(markets.itertuples(), deadlines):
            self._market = market.market_id
            if market.market_id not in terms.index:
                self.skip(SKIP_NO_TERMS)
                continue
            term = terms.loc[market.market_id]
            q_time = quote_time.get(market.ticker, pd.NaT)
            mine = chain[(chain["underlying"] == market.ticker) & (chain["option_type"] == "call")]
            if (mine.empty or pd.isna(q_time)
                    or (market.fetched_at - q_time).total_seconds() > STALE_CHAIN_S):
                self.skip(SKIP_NO_CHAIN)
                continue
            closes = expiry_closes(sorted(mine["expiry"].dropna().unique()), self._sessions)
            expiry = next((e for e, c in closes.items() if pd.notna(c) and c == deadline), None)
            if expiry is None:
                self.skip(SKIP_NO_EXPIRY)
                continue
            calls = mine[mine["expiry"] == expiry]
            years = (deadline - q_time).total_seconds() / (365.0 * 86400.0)

            def above(level, calls=calls, years=years):
                return prob_above(calls, float(level), rate, years)

            fair = self.fair_yes(above, {**term.to_dict(), "ticker": market.ticker,
                                         "cycle_id": cycle_id, "deadline": deadline})
            if fair is None:
                continue
            if not np.isfinite(fair):
                self.skip(SKIP_NO_STRIKES)
                continue
            fair = float(np.clip(fair, 0.0, 1.0))
            rows.append({"market_id": market.market_id, "ticker": market.ticker,
                         "deadline": deadline, "fair": float(fair),
                         "yes_direction": self.yes_direction(term), "yes_vol": None,
                         "contract_type": self.contract_type,
                         "level": self.level(term), "period": self.period})
        return pd.DataFrame(rows)

    def level(self, term) -> float:
        return float(term.get("strike")) if pd.notna(term.get("strike")) else float("nan")
