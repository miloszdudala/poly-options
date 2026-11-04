"""Polymarket daily "X Up or Down" markets on stocks: the strike is that day's
open, the first regular-session 5-minute bar's open."""
import pandas as pd

import data
from _chain import SKIP_NO_OPEN, ChainPriced


class PolyUpDownDailyStock(ChainPriced):
    name = "poly-up-down-daily-stock"
    contract_type = "up_down"
    period = "daily"

    def fair_yes(self, above, market):
        opening = self.opening_price(market["ticker"], market["deadline"],
                                     pd.Timestamp(data.cycle_start(market["cycle_id"])))
        if opening is None:
            self.skip(SKIP_NO_OPEN)
            return None
        up = above(opening)
        return up if market.get("outcome_0") == "Up" else 1.0 - up

    def yes_direction(self, term):
        return "up" if term.get("outcome_0") == "Up" else "down"

    @staticmethod
    def opening_price(ticker: str, deadline: pd.Timestamp, now: pd.Timestamp) -> float | None:
        """The 09:30 ET bar's open on the deadline's own date, once it has started."""
        bars = data.session_bars_5m(ticker)
        if bars.empty:
            return None
        eastern = bars["bar_time"].dt.tz_convert("America/New_York")
        day = deadline.tz_convert("America/New_York").date()
        first = bars[(eastern.dt.date == day) & (eastern.dt.hour == 9)
                     & (eastern.dt.minute == 30) & (bars["bar_time"] <= now)]
        return None if first.empty else float(first["open"].iloc[0])
