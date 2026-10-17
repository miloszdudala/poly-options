"""Polymarket CLOB order books: full depth, both outcome tokens, every snapshot.

Liquidity is thin, so only the complete book is useful; mid / last / top-of-book
are never stored on their own.
"""
from __future__ import annotations

from config import markets_cfg
from storage import raw_enabled, write_raw, write_table
from util import Http, chunked, get_logger, parallel_map, parse_epoch, utc_iso

log = get_logger("poly.books")


def _levels(book: dict, token_meta: dict, cycle_id: str, fetched_at: str) -> list[dict]:
    """Flatten one book into one row per price level, both sides."""
    rows: list[dict] = []
    book_ts = parse_epoch(book.get("timestamp"))
    common = {
        "cycle_id": cycle_id,
        "fetched_at_utc": fetched_at,
        "book_timestamp_utc": book_ts.isoformat() if book_ts else None,
        "book_hash": book.get("hash"),
        "token_id": book.get("asset_id"),
        "condition_id": book.get("market"),
        "market_id": token_meta.get("market_id"),
        "event_slug": token_meta.get("event_slug"),
        "ticker": token_meta.get("ticker"),
        "contract_type": token_meta.get("contract_type"),
        "strike": token_meta.get("strike"),
        "barrier": token_meta.get("barrier"),
        # Carried onto every level row on purpose. Without it (ticker, strike,
        # contract_type) is NOT unique - MSFT close_above 510 exists in four
        # different months at once - so any group-by on the obvious keys
        # silently blends books from different expiries into one.
        "end_date": token_meta.get("end_date"),
        "period": token_meta.get("period"),
        "outcome": token_meta.get("outcome"),
        "outcome_index": token_meta.get("outcome_index"),
        "tick_size": book.get("tick_size"),
        "min_order_size": book.get("min_order_size"),
        "neg_risk": book.get("neg_risk"),
        "book_last_trade_price": book.get("last_trade_price") or None,
    }
    for side in ("bids", "asks"):
        parsed: list[tuple[float, float]] = []
        for level in book.get(side) or []:
            try:
                parsed.append((float(level["price"]), float(level["size"])))
            except (KeyError, TypeError, ValueError):
                log.warning("bad level on token %s: %r", book.get("asset_id"), level)
        # The CLOB returns both sides worst-price-first, so the best price is the
        # LAST element. level_rank keeps the venue's own ordering; depth_rank is
        # normalised so 0 is always top of book, whichever side you are reading.
        best_first = sorted(
            range(len(parsed)),
            key=lambda i: parsed[i][0],
            reverse=(side == "bids"),
        )
        depth_of = {orig: d for d, orig in enumerate(best_first)}
        for rank, (price, size) in enumerate(parsed):
            rows.append(dict(
                common,
                side="bid" if side == "bids" else "ask",
                level_rank=rank,
                depth_rank=depth_of[rank],
                price=price,
                size=size,
            ))
    return rows


def collect_books(http: Http, markets: list[dict], cycle_id: str,
                  table: str = "poly_books") -> dict:
    """Fetch full books for both outcome tokens of every tracked market.

    `table="poly_books_recheck"` is the second read of the same books seconds
    later (jobs._recheck_books), written under the same cycle_id.
    """
    cfg = markets_cfg()["polymarket"]
    base = cfg["clob_base"]
    batch_size = cfg["books_batch_size"]

    # token_id -> descriptive metadata, so flattened rows are self-describing.
    token_meta: dict[str, dict] = {}
    for m in markets:
        # Markets already resolved inside a still-open event are dropped by the
        # CLOB (404). Skip them here rather than logging thousands of failures.
        if m.get("closed") or m.get("accepting_orders") is False:
            continue
        for idx in (0, 1):
            tid = m.get(f"token_id_{idx}")
            if not tid:
                continue
            token_meta[str(tid)] = {
                "market_id": m.get("market_id"),
                "event_slug": m.get("event_slug"),
                "ticker": m.get("ticker"),
                "contract_type": m.get("contract_type"),
                "strike": m.get("strike"),
                "barrier": m.get("barrier"),
                "end_date": m.get("end_date"),
                "period": m.get("period"),
                "outcome": m.get(f"outcome_{idx}"),
                "outcome_index": idx,
            }

    token_ids = list(token_meta)
    log.info("fetching books for %d tokens (%d markets)", len(token_ids), len(markets))

    all_rows: list[dict] = []
    got_tokens: set[str] = set()
    books_by_token: dict[str, dict] = {}

    for batch in chunked(token_ids, batch_size):
        # Stamped per batch, not once per run. A single stamp claimed that
        # every book in the cycle was read at the same instant; on 2026-09-17
        # the books of one "15-minute" cycle were actually gathered over 126
        # minutes, so the column understated the spread by two hours and the
        # smear was invisible to anything reading the table.
        fetched_at = utc_iso()
        payload = http.post(
            f"{base}/books",
            venue="polymarket",
            identifier=f"books_batch_{batch[0][:12]}",
            json=[{"token_id": t} for t in batch],
        )
        if payload is None:
            # One failed batch must not stop the run; fall back to per-token gets.
            log.warning("batch failed, falling back to single books for %d tokens", len(batch))
            for tid in batch:
                single = http.get(f"{base}/book", venue="polymarket", identifier=tid,
                                  params={"token_id": tid})
                if single:
                    payload = payload or []
                    all_rows.extend(_levels(single, token_meta.get(tid, {}), cycle_id, fetched_at))
                    got_tokens.add(tid)
                    books_by_token[tid] = single
            continue

        if raw_enabled("books"):
            write_raw("polymarket", f"books_{batch[0][:16]}", payload, cycle_id)
        # The batch endpoint does NOT preserve request order - key on asset_id.
        for book in payload if isinstance(payload, list) else []:
            tid = str(book.get("asset_id") or "")
            meta = token_meta.get(tid)
            if meta is None:
                log.warning("book for unknown token %s", tid)
                meta = {}
            got_tokens.add(tid)
            books_by_token[tid] = book
            all_rows.extend(_levels(book, meta, cycle_id, fetched_at))

    missing = set(token_ids) - got_tokens
    if missing:
        log.warning("no book returned for %d tokens", len(missing))

    if all_rows:
        write_table(table, all_rows)

    # The book payload carries its own last_trade_price, which is written onto
    # every level row as book_last_trade_price - so there is no separate
    # last-trade table: it would duplicate a column we already store.
    return {
        "tokens_requested": len(token_ids),
        "tokens_returned": len(got_tokens),
        "levels": len(all_rows),
        "missing": len(missing),
    }, books_by_token


def collect_price_history(http: Http, markets: list[dict], cycle_id: str,
                          interval: str = "1d", fidelity: int = 60,
                          workers: int = 8) -> int:
    """Hourly price history per token.

    The first time a token is seen its WHOLE history is fetched
    (`interval=max`, hourly points back to listing), after that only the last
    day. `interval=1d` alone never backfilled anything: a market listed in
    August had one day of prices in the table and nothing before, which made
    the price-versus-outcome calibration impossible for every market that
    resolved before collection started.

    One request per token, so fetched concurrently: ~3.6k sequential round trips
    would take longer than the daily window comfortably allows.
    """
    from storage import read_meta, write_meta

    base = markets_cfg()["polymarket"]["clob_base"]
    fetched_at = utc_iso()
    backfilled: set[str] = set((read_meta("price_history_backfilled") or {}).get("tokens", []))

    jobs = []
    for m in markets:
        for idx in (0, 1):
            tid = m.get(f"token_id_{idx}")
            if tid:
                jobs.append((str(tid), idx, m))

    def fetch(job):
        tid, idx, m = job
        span = interval if tid in backfilled else "max"
        payload = http.get(f"{base}/prices-history", venue="polymarket",
                           identifier=f"hist_{tid}",
                           params={"market": tid, "interval": span,
                                   "fidelity": fidelity})
        if payload is None:
            return None, []
        out = []
        for point in payload.get("history") or []:
            ts = parse_epoch(point.get("t"))
            out.append({
                "cycle_id": cycle_id,
                "fetched_at_utc": fetched_at,
                "token_id": tid,
                "market_id": m.get("market_id"),
                "ticker": m.get("ticker"),
                "outcome_index": idx,
                "point_time_utc": ts.isoformat() if ts else None,
                "price": point.get("p"),
                "interval": span,
                "fidelity": fidelity,
            })
        return tid, out

    rows: list[dict] = []
    for got in parallel_map(fetch, jobs, workers=workers):
        if not got:
            continue
        tid, points = got
        if tid:
            backfilled.add(tid)
        rows += points
    if rows:
        write_table("poly_price_history", rows)
    write_meta("price_history_backfilled", {"tokens": sorted(backfilled)})
    return len(rows)


def collect_resolutions(http: Http, markets: list[dict], cycle_id: str) -> int:
    """Record outcomes for markets that have closed.

    `result` is the winning label ("Yes"/"No") once UMA has resolved, else
    None; `settled_at_utc` is Gamma's closedTime, which for a hit market is
    the moment the barrier printed. The table is append-only and rewritten in
    full on every daily run, so read the latest row per market_id.
    """
    fetched_at = utc_iso()
    rows = [resolution_row(m, cycle_id, fetched_at) for m in markets if m.get("closed")]
    if rows:
        write_table("resolutions", rows)
    return len(rows)


def resolution_row(m: dict, cycle_id: str, fetched_at: str | None = None) -> dict:
    return {
        "cycle_id": cycle_id,
        "fetched_at_utc": fetched_at or utc_iso(),
        "venue": "polymarket",
        "market_id": m.get("market_id"),
        "event_slug": m.get("event_slug"),
        "ticker": m.get("ticker"),
        "contract_type": m.get("contract_type"),
        "strike": m.get("strike"),
        "barrier": m.get("barrier"),
        "question": m.get("question"),
        "uma_status": m.get("umaResolutionStatus"),
        "end_date": m.get("end_date"),
        "result": m.get("result"),
        "settled_at_utc": _closed_time_iso(m.get("closed_time")),
        "outcome_prices": m.get("outcome_prices"),
        "created_at": m.get("created_at"),
    }


def _closed_time_iso(value) -> str | None:
    """Gamma's closedTime looks like '2026-09-17 17:26:54+00' - normalise it."""
    from util import parse_iso

    if not value:
        return None
    text = str(value).strip()
    if text.endswith("+00"):
        text = text[:-3] + "+00:00"
    parsed = parse_iso(text)
    return parsed.isoformat() if parsed else text
