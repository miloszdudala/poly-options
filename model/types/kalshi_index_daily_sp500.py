"""Kalshi daily S&P 500 markets: range at the close (KXINX) and above/below at
a time (KXINXU), priced from the SPX call spread."""
from _kalshi import KalshiIndex


class KalshiIndexDailySp500(KalshiIndex):
    name = "kalshi-index-daily-sp500"
    period = "daily"
    series = ("KXINX", "KXINXU")
    underlying = "SPX"
