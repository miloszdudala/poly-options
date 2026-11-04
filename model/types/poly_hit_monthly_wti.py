"""Polymarket monthly "will WTI hit $B" markets, priced as in `_wti.py`.

Fair = continuous one-touch on the contract that is Active at the deadline,
with USO's implied vol at the barrier's moneyness times the WTI / USO
realised-vol ratio. Touches count in ICE trading hours, not stock hours.
"""

import numpy as np
import pandas as pd

import _wti
import bet_types
import data
import fair as fairlib
from engine import STALE_CHAIN_S, expiry_closes
from sessions import nyse_sessions

SKIP_NO_RATIO = "WTI/USO vol ratio not set (the user's to set)"
SKIP_NO_PRICE = "no price for the contract Active at the deadline"
SKIP_NO_CHAIN = "no fresh USO chain"
SKIP_NO_EXPIRY = "no USO expiry at or after the deadline"
SKIP_TOUCHED = "barrier already printed in ICE hours"
SKIP_QUOTE_AFTER_BOOK = "option quote newer than the book"


class PolyHitMonthlyWti(bet_types.Polymarket):
    name = "poly-hit-monthly-wti"
    period = "monthly"
    contract_types = ("hit_up", "hit_down")

    def __init__(self):
        super().__init__()
        self._sessions = nyse_sessions()
        self._books = None
        self._seen: set[tuple[str, str]] = set()

    def skip_market(self, reason: str, market_id: str) -> None:
        if (reason, market_id) not in self._seen:
            self._seen.add((reason, market_id))
            self.skip(reason)

    def cycles(self, dates):
        return sorted(c for date in dates for c in data.session_cycles(date))

    def books(self, cycle_id):
        if self._books is None or self._books[0] != cycle_id:
            self._books = (cycle_id, data.books(cycle_id, self.contract_types))
        return self._books[1]

    def priced(self, cycle_id):
        books = self.books(cycle_id)
        if books.empty:
            return pd.DataFrame()
        mine = books[(books["ticker"] == "WTI") & (books["period"] == self.period)]
        markets = (mine.groupby("market_id")
                   .agg(contract_type=("contract_type", "first"), barrier=("barrier", "first"),
                        end_date=("end_date", "first"), now=("fetched_at_utc", "max"))
                   .reset_index())
        if markets.empty:
            return pd.DataFrame()
        if _wti.VOL_RATIO is None:
            for market_id in markets["market_id"]:
                self.skip_market(SKIP_NO_RATIO, str(market_id))
            return pd.DataFrame()
        chain = data.option_chain(cycle_id, ("USO",))
        q_time = data.quote_times(cycle_id).get("USO", pd.NaT)
        uso_spot = data.cboe_spot(cycle_id).set_index("symbol")["last_price"].get("USO")
        rate = data.risk_free_rate(markets["now"].max())
        closes = expiry_closes(sorted(chain["expiry"].dropna().unique()), self._sessions) \
            if len(chain) else {}
        bars = data.bars_5m()
        rows = []
        for m in markets.itertuples():
            market_id, now, deadline = str(m.market_id), m.now, m.end_date
            barrier, is_up = float(m.barrier), m.contract_type == "hit_up"
            year, month = _wti.contract_to_price(now, deadline, self._sessions)
            symbol = _wti.cl_symbol(year, month)
            spot = float(data.spot_at(pd.Series([now]), pd.Series([symbol])).iloc[0])
            if not np.isfinite(spot):
                self.skip_market(SKIP_NO_PRICE, market_id)
                continue
            if self.touched(bars, symbol, barrier, is_up, market_id, deadline, now):
                self.skip_market(SKIP_TOUCHED, market_id)
                continue
            if (chain.empty or pd.isna(q_time) or uso_spot is None
                    or (now - q_time).total_seconds() > STALE_CHAIN_S):
                self.skip_market(SKIP_NO_CHAIN, market_id)
                continue
            if q_time > now:
                self.skip_market(SKIP_QUOTE_AFTER_BOOK, market_id)
                continue
            after = sorted(c for c in closes.values() if pd.notna(c) and c >= deadline)
            if not after:
                self.skip_market(SKIP_NO_EXPIRY, market_id)
                continue
            expiry = next(e for e, c in closes.items() if c == after[0])
            years_expiry = (after[0] - q_time).total_seconds() / (365.0 * 86400.0)
            smile = fairlib.build_smile(chain, float(uso_spot), expiry, years_expiry,
                                        years_expiry, rate)
            sigma, _ = _wti.vol_at_same_moneyness(smile, spot, barrier, _wti.VOL_RATIO)
            years = max((deadline - now).total_seconds(), 0.0) / (365.0 * 86400.0)
            if not (np.isfinite(sigma) and sigma > 0):
                self.skip_market(SKIP_NO_CHAIN, market_id)
                continue
            rows.append({"market_id": market_id, "ticker": "WTI", "deadline": deadline,
                         "fair": float(fairlib.one_touch(spot, barrier, years, sigma, rate,
                                                         "up" if is_up else "down")),
                         "yes_direction": "up" if is_up else "down", "yes_vol": "wild",
                         "contract_type": m.contract_type, "level": barrier,
                         "period": self.period})
        return pd.DataFrame(rows)

    @staticmethod
    def touched(bars, symbol, barrier, is_up, market_id, deadline, now) -> bool:
        """Has a finished 5-minute bar of `symbol` printed the barrier in ICE
        hours since the market began being watched?"""
        start = data.watch_start_for(market_id, deadline, "monthly")
        mine = bars[bars["symbol"] == symbol]
        if mine.empty or pd.isna(start):
            return False
        mine = mine[(mine["bar_time"] >= start)
                    & (mine["bar_time"] + pd.Timedelta(minutes=5) <= now)]
        mine = mine[_wti.in_ice_hours(mine["bar_time"]).values]
        if is_up:
            return bool((mine["high"] >= barrier).any())
        return bool((mine["low"] <= barrier).any())


