"""Polymarket discovery: walk every open event via Gamma keyset pagination.

Offset pagination is rejected by /events/keyset (confirmed in the API's own
openapi.json), so the cursor parameter `after_cursor` is the only way to reach
past the first couple of thousand events.
"""
from __future__ import annotations

import json
from typing import Any

from config import markets_cfg
from poly_parse import parse_market, parse_ticker
from storage import write_meta, write_raw, write_table
from util import Http, get_logger, utc_iso

log = get_logger("poly.discover")


def _norm_list(value: Any) -> list:
    """Gamma returns some list fields as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def walk_events(http: Http, cycle_id: str) -> list[dict]:
    """Page through every open event. Returns the raw event dicts."""
    cfg = markets_cfg()["polymarket"]
    base = f"{cfg['gamma_base']}/events/keyset"
    limit = cfg["page_limit"]
    events: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        params = {"limit": limit, "closed": "false"}
        if cursor:
            params["after_cursor"] = cursor
        payload = http.get(
            base, venue="polymarket", identifier=f"events_keyset_page{page}", params=params
        )
        if payload is None:
            log.error("discovery stopped early at page %d", page)
            break
        batch = payload.get("events") or []
        if not batch:
            break
        events.extend(batch)
        page += 1
        cursor = payload.get("next_cursor")
        if not cursor:
            break
        if page > 1000:  # safety valve against a cursor that never terminates
            log.error("discovery hit page cap at %d pages", page)
            break
    log.info("discovery walked %d pages, %d open events", page, len(events))
    return events


def event_matches(event: dict, disc: dict) -> tuple[bool, str]:
    """Apply the title-pattern + ticker filter. Returns (keep, reason)."""
    title = event.get("title") or ""
    low = title.lower()

    if not any(p in low for p in disc["title_patterns"]):
        return False, "no title pattern"

    ticker = parse_ticker(title)
    if not ticker:
        return False, "no parenthesised ticker"
    if ticker in set(disc.get("ticker_blocklist") or []):
        return False, f"blocklisted token {ticker}"
    if ticker in set(disc.get("ticker_exclude") or []):
        return False, f"excluded ticker {ticker}"

    tags = {(t.get("slug") or "").lower() for t in (event.get("tags") or [])}
    blocked = tags & set(disc.get("tag_blocklist") or [])
    if blocked:
        return False, f"blocked tag {sorted(blocked)}"

    allow = set(disc.get("ticker_allowlist") or [])
    if allow and ticker not in allow:
        return False, f"{ticker} not in allowlist"

    return True, "ok"


def discover(http: Http, cycle_id: str) -> list[dict]:
    """Find tracked markets and write the poly_markets metadata table.

    Returns one row per market, each carrying both outcome token ids.
    """
    cfg = markets_cfg()["polymarket"]
    disc = cfg["discovery"]
    events = walk_events(http, cycle_id)

    rows: list[dict] = []
    unparsed: list[dict] = []
    kept: list[dict] = []
    kept_events = 0

    for event in events:
        keep, _reason = event_matches(event, disc)
        if not keep:
            continue
        kept.append(event)
        kept_events += 1
        ticker = parse_ticker(event.get("title") or "")
        tags = [t.get("slug") for t in (event.get("tags") or [])]

        for market in event.get("markets") or []:
            outcomes = _norm_list(market.get("outcomes"))
            token_ids = _norm_list(market.get("clobTokenIds"))
            parsed = parse_market(
                market.get("question") or "",
                event_title=event.get("title") or "",
                group_item_title=market.get("groupItemTitle"),
                outcomes=outcomes,
            )
            if parsed.contract_type == "unparsed":
                unparsed.append({
                    "market_id": market.get("id"),
                    "question": market.get("question"),
                    "event_title": event.get("title"),
                    "group_item_title": market.get("groupItemTitle"),
                    "reason": parsed.parse_reason,
                })

            row = {
                "cycle_id": cycle_id,
                "fetched_at_utc": utc_iso(),
                "event_id": event.get("id"),
                "event_slug": event.get("slug"),
                "event_title": event.get("title"),
                "event_end_date": event.get("endDate"),
                "market_id": market.get("id"),
                "market_slug": market.get("slug"),
                "condition_id": market.get("conditionId"),
                "question": market.get("question"),
                "group_item_title": market.get("groupItemTitle"),
                "ticker": parsed.ticker or ticker,
                "contract_type": parsed.contract_type,
                "strike": parsed.strike,
                "barrier": parsed.barrier,
                "range_low": parsed.range_low,
                "range_high": parsed.range_high,
                "direction": parsed.direction,
                "period": parsed.period,
                "parse_reason": parsed.parse_reason,
                "end_date": market.get("endDate") or market.get("endDateIso"),
                "outcomes": outcomes,
                "token_id_0": token_ids[0] if len(token_ids) > 0 else None,
                "token_id_1": token_ids[1] if len(token_ids) > 1 else None,
                "outcome_0": outcomes[0] if len(outcomes) > 0 else None,
                "outcome_1": outcomes[1] if len(outcomes) > 1 else None,
                "tick_size": market.get("orderPriceMinTickSize"),
                "min_order_size": market.get("orderMinSize"),
                "maker_base_fee": market.get("makerBaseFee"),
                "taker_base_fee": market.get("takerBaseFee"),
                "fee_type": market.get("feeType"),
                "fees_enabled": market.get("feesEnabled"),
                "fee_schedule": market.get("feeSchedule"),
                "neg_risk": market.get("negRisk"),
                "accepting_orders": market.get("acceptingOrders"),
                "active": market.get("active"),
                "closed": market.get("closed"),
                "volume": market.get("volumeNum") or market.get("volume"),
                "liquidity": market.get("liquidityClob") or market.get("liquidity"),
                "last_trade_price": market.get("lastTradePrice"),
                "best_bid": market.get("bestBid"),
                "best_ask": market.get("bestAsk"),
                "spread": market.get("spread"),
                "event_tags": tags,
                # Rules text, verbatim - the barrier model needs to know whether a
                # hit is checked on intraday extremes or on closes, and on whose feed.
                "rules_text": market.get("description"),
                "resolution_source": market.get("resolutionSource"),
                "umaResolutionStatus": market.get("umaResolutionStatus"),
            }
            rows.append(row)

    log.info(
        "discovery kept %d events / %d markets (%d unparsed)",
        kept_events, len(rows), len(unparsed),
    )

    # Archive the matched events in full, plus a slim index of everything that was
    # walked. Storing all ~20k open events verbatim cost ~88 MB per run for the
    # ~130 we actually track; the index preserves the audit trail (what existed,
    # and what the filter chose not to keep) for a fraction of a percent of that.
    write_raw("polymarket", "events_matched", kept, cycle_id)
    write_raw("polymarket", "events_index", [
        {
            "id": e.get("id"),
            "slug": e.get("slug"),
            "title": e.get("title"),
            "endDate": e.get("endDate"),
            "tags": [t.get("slug") for t in (e.get("tags") or [])],
            "matched": bool(event_matches(e, disc)[0]),
        }
        for e in events
    ], cycle_id)
    if unparsed:
        for item in unparsed[:20]:
            log.warning("UNPARSED market %s: %r (%s)",
                        item["market_id"], item["question"], item["reason"])
        write_table("poly_unparsed", [dict(u, cycle_id=cycle_id, fetched_at_utc=utc_iso())
                                      for u in unparsed])

    if rows:
        write_table("poly_markets", rows)
        # Metadata snapshot refreshed in place, for quick lookup by later jobs.
        write_meta("poly_markets_latest", {"cycle_id": cycle_id, "markets": rows})
    return rows


def tracked_markets() -> list[dict]:
    """Load the most recent discovery result."""
    from storage import read_meta

    meta = read_meta("poly_markets_latest")
    return (meta or {}).get("markets", [])
