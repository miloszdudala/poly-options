"""Live stock prices from Tiingo, read just before the Polymarket books.

The option chain is Cboe's 15-minute-delayed feed and the 5-minute bars only
count once finished, so neither shows a barrier printed in the last few
minutes, while Polymarket does: MSFT went through 525 at 18:15 on 2026-11-13,
seconds before the 18:15:31 book, and the engine bought the NO at 1c.

Tiingo's free plan gives a price built from every US venue (not IEX alone),
with the day's regular-session open, high and low (they match our 5-minute
bars to the cent). One request carries every ticker. The key is read from
TIINGO_API_KEY or the repo's `.env`; without one this step is skipped.
Rows go to `underlying_quotes` with `source = 'tiingo'`.
"""
from __future__ import annotations

import os
from pathlib import Path

from config import underlyings
from storage import write_table
from util import get_logger, utc_iso

log = get_logger("live")

URL = "https://api.tiingo.com/iex/"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def api_key() -> str | None:
    key = os.environ.get("TIINGO_API_KEY")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "TIINGO_API_KEY" and value.strip():
                return value.strip()
    return None


def collect_live(http, cycle_id: str) -> dict:
    key = api_key()
    if not key:
        log.warning("no TIINGO_API_KEY: live prices skipped")
        return {"tiingo": 0}
    symbols = [u.symbol for u in underlyings() if not u.is_index]
    fetched = utc_iso()
    payload = http.get(URL, venue="tiingo", identifier="batch",
                       params={"tickers": ",".join(symbols)},
                       headers={"Authorization": f"Token {key}"})
    rows = [{
        "cycle_id": cycle_id, "fetched_at_utc": fetched, "source": "tiingo",
        "symbol": str(q.get("ticker", "")).upper(), "last_price": q.get("tngoLast"),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "prev_close": q.get("prevClose"), "volume": q.get("volume"),
        "source_quote_time_utc": q.get("timestamp"),
    } for q in (payload or []) if isinstance(q, dict) and q.get("tngoLast")]
    write_table("underlying_quotes", rows)
    return {"tiingo": len(rows)}
