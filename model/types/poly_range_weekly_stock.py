"""Polymarket weekly "will X close at $L-$H" markets on stocks: the difference
of two close-aboves at the same expiry. An open-ended range ("above $L" or
"below $H") is one close-above."""
import math

import pandas as pd

from _chain import ChainPriced


def _num(value):
    return float(value) if value is not None and pd.notna(value) else None


class PolyRangeWeeklyStock(ChainPriced):
    name = "poly-range-weekly-stock"
    contract_type = "range"
    period = "weekly"

    def fair_yes(self, above, market):
        low, high = _num(market.get("range_low")), _num(market.get("range_high"))
        if low is not None and high is not None:
            return above(low) - above(high)
        if low is not None:
            return above(low)
        if high is not None:
            return 1.0 - above(high)
        return math.nan

    def yes_direction(self, term):
        low, high = _num(term.get("range_low")), _num(term.get("range_high"))
        if low is not None and high is None:
            return "up"
        if high is not None and low is None:
            return "down"
        return None

    def level(self, term):
        low = _num(term.get("range_low"))
        return low if low is not None else (_num(term.get("range_high")) or math.nan)
