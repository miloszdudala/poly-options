"""Polymarket daily "X Up or Down" markets on stocks: Up if today's close beats
the previous trading day's close, so the strike is that close. Checked on 760
resolved markets (2026-11-04): previous close matches the result 98.7% of the
time, today's open 74%.

"X Opens Up or Down" markets compare the open with the previous close instead:
they are decided at 09:30 ET and no chain expiring at the close prices them, so
they are skipped and counted.
"""
import pandas as pd

import data
from _chain import SKIP_NO_PREVIOUS_CLOSE, SKIP_OPENS_MARKET, ChainPriced


class PolyUpDownDailyStock(ChainPriced):
    name = "poly-up-down-daily-stock"
    contract_type = "up_down"
    period = "daily"

    def fair_yes(self, above, market):
        if "opens-up-or-down" in str(market.get("event_slug")):
            self.skip(SKIP_OPENS_MARKET)
            return None
        close = self.previous_close(market["ticker"], market["deadline"])
        if close is None:
            self.skip(SKIP_NO_PREVIOUS_CLOSE)
            return None
        up = above(close)
        return up if market.get("outcome_0") == "Up" else 1.0 - up

    def yes_direction(self, term):
        return "up" if term.get("outcome_0") == "Up" else "down"

    @staticmethod
    def previous_close(ticker: str, deadline: pd.Timestamp) -> float | None:
        """The last regular-session print before the deadline's own date."""
        bars = data.session_bars_5m(ticker)
        if bars.empty:
            return None
        eastern = bars["bar_time"].dt.tz_convert("America/New_York")
        day = deadline.tz_convert("America/New_York").date()
        before = bars[eastern.dt.date < day]
        return None if before.empty else float(before["close"].iloc[-1])
