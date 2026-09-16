"""Kalshi market data (public, read-only).

Field names verified live 2026-09-16 and they differ from the older API shape:
  * books come back as `orderbook_fp` with `yes_dollars` / `no_dollars`
  * prices are dollar strings (already probability units), not integer cents
  * sizes / volume / open interest carry an `_fp` suffix
Raw values are kept alongside the normalised probabilities.

Book convention: Kalshi lists resting BIDS on each side. A yes ask is not
published directly; it is implied by a no bid at 1 - price. Both sides are
stored as given, plus a derived yes-equivalent ask, so nothing is lost.
"""
from __future__ import annotations

from config import markets_cfg
from storage import raw_enabled, write_meta, write_raw, write_raw_once, write_table
from util import Http, get_logger, parallel_map, utc_iso

log = get_logger("kalshi")


def _f(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def all_series() -> list[str]:
    groups = markets_cfg()["kalshi"]["series"]
    out: list[str] = []
    for names in groups.values():
        out.extend(names)
    return out


def fetch_markets(http: Http, series_ticker: str, cycle_id: str) -> list[dict]:
    """All open markets for one series, following the cursor."""
    cfg = markets_cfg()["kalshi"]
    base = cfg["base"]
    fetched_at = utc_iso()
    rows: list[dict] = []
    cursor: str | None = None
    page = 0

    while True:
        params = {"series_ticker": series_ticker, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        payload = http.get(f"{base}/markets", venue="kalshi",
                           identifier=f"{series_ticker}_p{page}", params=params)
        if not payload:
            break
        markets = payload.get("markets") or []
        if not markets:
            break
        if page == 0 and raw_enabled("market_rules"):
            write_raw_once("kalshi", f"markets_{series_ticker}", payload, cycle_id)

        for m in markets:
            rows.append({
                "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                "series_ticker": series_ticker,
                "event_ticker": m.get("event_ticker"),
                "ticker": m.get("ticker"),
                "title": m.get("title"),
                "subtitle": m.get("subtitle"),
                "yes_sub_title": m.get("yes_sub_title"),
                "no_sub_title": m.get("no_sub_title"),
                "market_type": m.get("market_type"),
                "strike_type": m.get("strike_type"),
                "floor_strike": m.get("floor_strike"),
                "cap_strike": m.get("cap_strike"),
                "custom_strike": m.get("custom_strike"),
                "status": m.get("status"),
                "open_time": m.get("open_time"),
                "close_time": m.get("close_time"),
                "expiration_time": m.get("expiration_time"),
                "expected_expiration_time": m.get("expected_expiration_time"),
                # Already in dollars == probability units.
                "yes_bid": _f(m.get("yes_bid_dollars")),
                "yes_ask": _f(m.get("yes_ask_dollars")),
                "no_bid": _f(m.get("no_bid_dollars")),
                "no_ask": _f(m.get("no_ask_dollars")),
                "yes_bid_size": _f(m.get("yes_bid_size_fp")),
                "yes_ask_size": _f(m.get("yes_ask_size_fp")),
                "last_price": _f(m.get("last_price_dollars")),
                "previous_price": _f(m.get("previous_price_dollars")),
                "volume": _f(m.get("volume_fp")),
                "volume_24h": _f(m.get("volume_24h_fp")),
                "open_interest": _f(m.get("open_interest_fp")),
                "liquidity": _f(m.get("liquidity_dollars")),
                "notional_value": _f(m.get("notional_value_dollars")),
                "result": m.get("result"),
                "can_close_early": m.get("can_close_early"),
                "rules_primary": m.get("rules_primary"),
                "rules_secondary": m.get("rules_secondary"),
                "settlement_sources": m.get("settlement_sources"),
                # Raw, pre-normalisation.
                "raw_yes_bid_dollars": m.get("yes_bid_dollars"),
                "raw_yes_ask_dollars": m.get("yes_ask_dollars"),
                "raw_last_price_dollars": m.get("last_price_dollars"),
                "raw_volume_fp": m.get("volume_fp"),
                "raw_open_interest_fp": m.get("open_interest_fp"),
            })

        cursor = payload.get("cursor")
        page += 1
        if not cursor or page > 50:
            break

    return rows


def fetch_orderbook(http: Http, market: dict, cycle_id: str) -> tuple[list[dict], dict | None]:
    """Full book for one market, every level, both sides.

    Returns (flattened rows, raw payload). The raw payload is handed back rather
    than archived here: one gzip per market meant thousands of ~300 byte files
    per cycle, each consuming a 4 KiB block, so collect() batches them instead.
    """
    cfg = markets_cfg()["kalshi"]
    ticker = market["ticker"]
    payload = http.get(f"{cfg['base']}/markets/{ticker}/orderbook",
                       venue="kalshi", identifier=ticker,
                       params={"depth": cfg["orderbook_depth"]})
    if not payload:
        return [], None

    fetched_at = utc_iso()
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    rows: list[dict] = []

    for side_key, side in (("yes_dollars", "yes"), ("no_dollars", "no"),
                           ("yes", "yes"), ("no", "no")):
        levels = book.get(side_key)
        if not levels:
            continue
        # Cents only on the legacy `yes`/`no` keys; `_dollars` keys are already
        # probability units.
        is_cents = not side_key.endswith("_dollars")
        parsed: list[tuple[float, float, str, str]] = []
        for level in levels:
            try:
                raw_price, raw_size = level[0], level[1]
                price = float(raw_price) / (100.0 if is_cents else 1.0)
                size = float(raw_size)
            except (TypeError, ValueError, IndexError):
                log.warning("bad kalshi level %s: %r", ticker, level)
                continue
            parsed.append((price, size, str(raw_price), str(raw_size)))

        # Kalshi lists bids ascending; normalise so depth_rank 0 is the best bid.
        order = sorted(range(len(parsed)), key=lambda i: parsed[i][0], reverse=True)
        depth_of = {orig: d for d, orig in enumerate(order)}
        for rank, (price, size, raw_price, raw_size) in enumerate(parsed):
            rows.append({
                "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                "series_ticker": market.get("series_ticker"),
                "event_ticker": market.get("event_ticker"),
                "ticker": ticker,
                "side": side,
                "level_rank": rank,
                "depth_rank": depth_of[rank],
                "price_prob": price,
                "size": size,
                "raw_price": raw_price,
                "raw_size": raw_size,
                "price_units": "cents" if is_cents else "dollars",
            })

    return rows, payload


def wants_book(market: dict) -> bool:
    """Whether this market earns a separate depth request.

    Every market's top of book is already stored: GET /markets returns
    yes_bid/yes_ask and their sizes for all of them in ~10 paged requests. Depth
    costs one request EACH, and most of the ladder does not deserve one - of
    3,519 open markets on 2026-09-16, 268 had ever traded and ~1,000 were quoted
    more than 20c wide having never traded. Those are ladder fillers: a quote
    that wide is not an offer anyone can lift, so its depth tells you nothing.

    Kept if it has ever traded, or is quoted tightly enough to be liftable, or
    belongs to a series we always want priced. A market with no quote at all is
    kept only if it has traded - otherwise there is nothing there.
    """
    cfg = markets_cfg()["kalshi"].get("book_filter") or {}
    if not cfg.get("enabled", False):
        return True
    if market.get("series_ticker") in set(cfg.get("always_series") or []):
        return True
    if (market.get("open_interest") or 0) > 0 or (market.get("volume") or 0) > 0:
        return True
    bid, ask = market.get("yes_bid"), market.get("yes_ask")
    if bid is None or ask is None:
        return False
    return (ask - bid) <= cfg.get("max_spread", 0.20)


def collect(http: Http, cycle_id: str, with_books: bool = True) -> dict:
    """Markets + full books for every configured series."""
    market_rows: list[dict] = []
    for series in all_series():
        got = fetch_markets(http, series, cycle_id)
        log.info("%-18s %d open markets", series, len(got))
        market_rows += got

    if market_rows:
        write_table("kalshi_markets", market_rows)
        # Kept so the option strike filter can see which index levels we track.
        write_meta("kalshi_markets_latest", {"cycle_id": cycle_id, "markets": [
            {k: m.get(k) for k in ("series_ticker", "ticker", "floor_strike", "cap_strike")}
            for m in market_rows]})

    book_rows: list[dict] = []
    book_markets: list[dict] = []
    if with_books:
        # Kalshi only exposes one book per request, so fetch them concurrently;
        # sequentially this alone overran the 15-minute snapshot cadence.
        book_markets = [m for m in market_rows if wants_book(m)]
        skipped = len(market_rows) - len(book_markets)
        if skipped:
            log.info("depth: %d markets, %d skipped as unliftable (top of book "
                     "still stored for all %d)", len(book_markets), skipped,
                     len(market_rows))
        workers = markets_cfg()["kalshi"].get("book_workers", 8)
        results = parallel_map(
            lambda m: fetch_orderbook(http, m, cycle_id), book_markets, workers=workers)
        raw_books: dict[str, dict] = {}
        for market, got in zip(book_markets, results):
            if not got:
                continue
            rows, payload = got
            book_rows += rows
            if payload is not None:
                raw_books[market["ticker"]] = payload
        if raw_books and raw_enabled("books"):
            # One archive per cycle, keyed by ticker, instead of one file each.
            write_raw("kalshi", "orderbooks", raw_books, cycle_id)
        if book_rows:
            write_table("kalshi_books", book_rows)

    return {"kalshi_markets": len(market_rows),
            "kalshi_books_fetched": len(book_markets),
            "kalshi_book_levels": len(book_rows)}
