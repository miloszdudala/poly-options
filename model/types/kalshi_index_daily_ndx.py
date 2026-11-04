"""Kalshi daily Nasdaq-100 markets: range at the close (KXNASDAQ100) and
above/below at a time (KXNASDAQ100U), priced from the NDX call spread."""
from _kalshi import KalshiIndex


class KalshiIndexDailyNdx(KalshiIndex):
    name = "kalshi-index-daily-ndx"
    period = "daily"
    series = ("KXNASDAQ100", "KXNASDAQ100U")
    underlying = "NDX"
