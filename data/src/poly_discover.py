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
from storage import raw_enabled, read_meta, write_meta, write_raw_once, write_table
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


def walk_events(http: Http, cycle_id: str,
                cursor: str | None = None) -> tuple[list[dict], str | None, bool]:
    """Page through open events, starting after `cursor` (None = the start).

    Returns (events, cursor, complete). When a page cannot be fetched - most
    often the snapshot deadline - the walk stops and returns the cursor of the
    page it failed on, so the next run can pick up there instead of re-walking
    the pages it already has.
    """
    cfg = markets_cfg()["polymarket"]
    base = f"{cfg['gamma_base']}/events/keyset"
    limit = cfg["page_limit"]
    events: list[dict] = []
    page = 0
    complete = True
    while True:
        params = {"limit": limit, "closed": "false"}
        if cursor:
            params["after_cursor"] = cursor
        payload = http.get(
            base, venue="polymarket", identifier=f"events_keyset_page{page}", params=params
        )
        if payload is None:
            log.error("discovery stopped early at page %d", page)
            complete = False
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
    return events, (None if complete else cursor), complete


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


def parse_outcome_prices(value: Any) -> list[float] | None:
    """Gamma's `outcomePrices`: '["1", "0"]' once resolved, live prices before."""
    prices = _norm_list(value)
    out = []
    for p in prices:
        try:
            out.append(float(p))
        except (TypeError, ValueError):
            return None
    return out or None


def resolution_result(market: dict, outcomes: list[str]) -> str | None:
    """The winning outcome label, once UMA has resolved the market.

    Gamma pins `outcomePrices` to exactly 1/0 on resolution, so the label whose
    price is 1 won. Anything else (a 50-50 settlement, or an unresolved but
    closed market) is returned as None and the raw prices are stored alongside.
    """
    if (market.get("umaResolutionStatus") or "").lower() != "resolved":
        return None
    prices = parse_outcome_prices(market.get("outcomePrices"))
    if not prices or len(prices) != len(outcomes):
        return None
    winners = [o for o, p in zip(outcomes, prices) if p == 1.0]
    losers = [p for p in prices if p == 0.0]
    if len(winners) == 1 and len(losers) == len(prices) - 1:
        return str(winners[0])
    return None


def market_row(event: dict, market: dict, cycle_id: str) -> dict:
    """One flat poly_markets row for a market inside its event."""
    ticker = parse_ticker(event.get("title") or "")
    tags = [t.get("slug") for t in (event.get("tags") or [])]
    outcomes = _norm_list(market.get("outcomes"))
    token_ids = _norm_list(market.get("clobTokenIds"))
    parsed = parse_market(
        market.get("question") or "",
        event_title=event.get("title") or "",
        group_item_title=market.get("groupItemTitle"),
        outcomes=outcomes,
    )
    return {
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
        # Lifecycle. A hit market resolves the moment its barrier prints, so
        # closed_time IS the touch time; outcome_prices pins to 1/0 on
        # resolution and `result` names the winning label. created_at matters
        # because Polymarket lists new barriers near spot around 12:40 ET every
        # session, and a quarter of the ones that touch do so within four hours.
        "created_at": market.get("createdAt"),
        "start_date": market.get("startDate") or market.get("startDateIso"),
        "closed_time": market.get("closedTime"),
        "outcome_prices": market.get("outcomePrices"),
        "result": resolution_result(market, outcomes),
    }


# A walk interrupted this long ago is started again rather than resumed: the
# events it kept would be stale, and the daily walk will have replaced it.
RESUME_MAX_AGE_HOURS = 6


def _load_resume(started) -> tuple[str | None, list[dict]]:
    """Cursor and matched events of an interrupted walk, if one is fresh."""
    import datetime as dt

    from util import parse_iso

    try:
        state = read_meta("discovery_resume")
    except ValueError:  # half-written by a run killed mid-write
        return None, []
    if not state or not state.get("cursor"):
        return None, []
    at = parse_iso(state.get("at_utc"))
    if at is None or started - at > dt.timedelta(hours=RESUME_MAX_AGE_HOURS):
        return None, []
    return state["cursor"], list(state.get("kept") or [])


def discover(http: Http, cycle_id: str) -> tuple[list[dict], bool]:
    """Find tracked markets and write the poly_markets metadata table.

    Returns (rows, complete): one row per market, each carrying both outcome
    token ids, and whether the walk reached the last page. At the US open
    Gamma serves the ~190-page walk at ~5 s a page (measured 2026-09-21/22,
    against ~0.4 s at 04:30 local), longer than a snapshot's whole budget. An
    interrupted walk therefore writes nothing: it saves its cursor and the
    events it matched, returns ([], False), and the next call resumes from
    there. Only a complete walk is written, so a partial one can never stand
    in for the full set of open events.
    """
    from util import now_utc

    cfg = markets_cfg()["polymarket"]
    disc = cfg["discovery"]
    started = now_utc()
    cursor, kept = _load_resume(started)
    if cursor:
        log.info("discovery resuming an interrupted walk (%d events kept so far)", len(kept))
    events, cursor, complete = walk_events(http, cycle_id, cursor)
    kept += [e for e in events if event_matches(e, disc)[0]]
    if not complete:
        write_meta("discovery_resume", {"at_utc": utc_iso(started), "cursor": cursor,
                                        "kept": kept})
        log.warning("discovery incomplete: %d events kept so far, resumes next run", len(kept))
        return [], False
    write_meta("discovery_resume", {})

    rows: list[dict] = []
    unparsed: list[dict] = []
    kept_events = len(kept)

    for event in kept:
        for market in event.get("markets") or []:
            row = market_row(event, market, cycle_id)
            if row["contract_type"] == "unparsed":
                unparsed.append({
                    "market_id": row["market_id"],
                    "question": row["question"],
                    "event_title": row["event_title"],
                    "group_item_title": row["group_item_title"],
                    "reason": row["parse_reason"],
                })
            rows.append(row)

    log.info(
        "discovery kept %d events / %d markets (%d unparsed)",
        kept_events, len(rows), len(unparsed),
    )

    # Archive the matched events, but only when the venue's own text changes -
    # rules and metadata are static per market, so re-storing them every run buys
    # nothing. This is the only raw we keep: if a settlement is ever disputed, it
    # is the untouched record of what the venue said the bet meant.
    if raw_enabled("market_rules"):
        write_raw_once("polymarket", "events_matched", kept, cycle_id)
    if unparsed:
        for item in unparsed[:20]:
            log.warning("UNPARSED market %s: %r (%s)",
                        item["market_id"], item["question"], item["reason"])
        write_table("poly_unparsed", [dict(u, cycle_id=cycle_id, fetched_at_utc=utc_iso())
                                      for u in unparsed])

    if rows:
        write_table("poly_markets", rows)
        # Metadata snapshot refreshed in place, for quick lookup by later jobs.
        # Markets from events that have since closed are kept from the previous
        # cache so their books stop but their outcomes are still collected.
        previous = (read_meta("poly_markets_latest") or {}).get("markets") or []
        fresh_ids = {str(r.get("market_id")) for r in rows}
        carried = [r for r in previous if str(r.get("market_id")) not in fresh_ids]
        write_meta("poly_markets_latest", {"cycle_id": cycle_id, "markets": rows + carried})
        from poly_events import remember_events
        remember_events({r.get("event_slug"): r.get("event_id") for r in rows if r.get("event_slug")})
    return rows, True


def tracked_markets() -> list[dict]:
    """Load the most recent discovery result."""
    from storage import read_meta

    meta = read_meta("poly_markets_latest")
    return (meta or {}).get("markets", [])
