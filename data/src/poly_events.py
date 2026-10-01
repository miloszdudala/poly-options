"""Keep the tracked-market cache current between full discoveries.

Two things the once-a-day walk of open events cannot see, measured 2026-09-17:

* Polymarket lists a fresh batch of barrier levels around 12:40 ET every
  session (164 new hit markets on 16-17 Sep, all in the 12:00 ET hour). They
  sit closest to spot, so they are the ones that trade and the ones that touch
  first - a quarter of touched markets resolved within four hours of listing.
  With discovery at 17:30 ET their books were never collected on listing day.
* An event that closes (a weekly ladder on Friday, a monthly one at month end)
  drops out of a `closed=false` walk before its markets are resolved, so the
  NO outcomes - every market that was *not* touched - were never recorded.

This module refreshes every known event (`GET /events?id=..&id=..`, up to
100 ids per request - verified 2026-09-18, 128 events in 2 requests, ~0.5 s
each; slug lookup is the fallback for an event whose id is unknown), merges
the result into the
`poly_markets_latest` cache, writes metadata rows for markets seen for the
first time, and writes a `resolutions` row the first time a market shows a
result. An event is retired from the refresh list once every market in it is
resolved.
"""
from __future__ import annotations

from config import markets_cfg
from storage import read_meta, write_meta, write_table
from util import Http, get_logger, utc_iso

log = get_logger("poly.events")

TRACKED_META = "tracked_events"


def known_events() -> dict[str, dict]:
    """slug -> {event_id, first_seen_utc, last_seen_utc, done, misses}."""
    return dict((read_meta(TRACKED_META) or {}).get("events", {}))


def remember_events(events_seen: dict[str, str | None], when: str | None = None) -> dict[str, dict]:
    """Add {slug: event_id} from a full discovery to the tracked set."""
    when = when or utc_iso()
    events = known_events()
    for slug, event_id in events_seen.items():
        if not slug:
            continue
        entry = events.setdefault(slug, {"first_seen_utc": when, "done": False})
        entry["last_seen_utc"] = when
        if event_id:
            entry["event_id"] = str(event_id)
    write_meta(TRACKED_META, {"events": events})
    return events


ID_BATCH = 100  # Gamma: "expected array length <= 100"


def _fetch_events(http: Http, base: str, tracked: dict[str, dict],
                  slugs: list[str]) -> tuple[dict[str, dict], set[str]]:
    """slug -> event payload, batched by id where the id is known.

    Also returns the slugs Gamma actually answered for. A request that failed
    (Http gives up and returns None) says nothing about whether the event
    exists, and must not count against it: on 2026-09-29 a three-hour outage
    retired 204 live events, every September monthly among them, and no
    outcome was recorded for any of them afterwards.
    """
    found: dict[str, dict] = {}
    answered: set[str] = set()
    with_id = [(s, tracked[s]["event_id"]) for s in slugs if tracked.get(s, {}).get("event_id")]
    for i in range(0, len(with_id), ID_BATCH):
        batch = with_id[i:i + ID_BATCH]
        payload = http.get(f"{base}/events", venue="polymarket",
                           identifier=f"events_by_id_{i // ID_BATCH}",
                           params=[("id", eid) for _, eid in batch] + [("limit", str(ID_BATCH))])
        if isinstance(payload, list):
            answered.update(s for s, _ in batch)
        for event in payload if isinstance(payload, list) else []:
            if event.get("slug"):
                found[event["slug"]] = event
    for slug in slugs:
        if slug in found or tracked.get(slug, {}).get("event_id"):
            continue
        payload = http.get(f"{base}/events", venue="polymarket",
                           identifier=f"event_{slug}", params={"slug": slug})
        if isinstance(payload, list):
            answered.add(slug)
            if payload:
                found[slug] = payload[0]
    return found, answered


def _all_resolved(event: dict) -> bool:
    markets = event.get("markets") or []
    if not markets:
        return bool(event.get("closed"))
    return all(
        m.get("closed") and (m.get("umaResolutionStatus") or "").lower() == "resolved"
        for m in markets
    )


def refresh_events(http: Http, cycle_id: str, slugs: list[str] | None = None) -> dict:
    """Re-read every live tracked event and merge it into the cache.

    Returns counts: events refreshed, markets new, markets newly resolved.
    """
    from poly_discover import market_row

    cfg = markets_cfg()["polymarket"]
    base = cfg["gamma_base"]
    cache = read_meta("poly_markets_latest") or {}
    old_rows: list[dict] = list(cache.get("markets") or [])
    by_id: dict[str, dict] = {str(r.get("market_id")): r for r in old_rows}

    tracked = known_events()
    # Anything in the cache that the tracked set has not heard of (first run
    # after this module was added) is adopted.
    cache_events = {r.get("event_slug"): r.get("event_id") for r in old_rows if r.get("event_slug")}
    if set(cache_events) - set(tracked):
        tracked = remember_events({s: e for s, e in cache_events.items() if s not in tracked})

    if slugs is None:
        slugs = [s for s, e in tracked.items() if not e.get("done")]
    if not slugs:
        return {"events_refreshed": 0, "markets_new": 0, "markets_resolved": 0}

    fetched_at = utc_iso()
    found, answered = _fetch_events(http, base, tracked, slugs)

    new_rows: list[dict] = []
    resolved_rows: list[dict] = []
    refreshed = 0
    for slug in slugs:
        event = found.get(slug)
        entry = tracked.setdefault(slug, {"first_seen_utc": fetched_at, "done": False})
        if not event:
            if slug not in answered:
                continue   # the request failed; try again next tick
            # Gamma answered without the event (deleted or renamed). Give up
            # after a few misses rather than pay a request every tick forever.
            entry["misses"] = int(entry.get("misses") or 0) + 1
            if entry["misses"] >= 8:
                entry["done"] = True
                log.warning("retiring event %s after %d misses", slug, entry["misses"])
            continue
        entry["misses"] = 0
        entry["event_id"] = str(event.get("id") or entry.get("event_id") or "") or None
        refreshed += 1
        retiring = _all_resolved(event)
        for market in event.get("markets") or []:
            row = market_row(event, market, cycle_id)
            row["fetched_at_utc"] = fetched_at
            mid = str(row.get("market_id"))
            before = by_id.get(mid)
            if before is None:
                new_rows.append(row)
            # A retiring event writes every outcome, not only the ones new to
            # the cache: the daily discovery walk can put a result into the
            # cache first, and on 2026-09-25 that left 57 NO outcomes of the
            # week unrecorded when the event was retired and pruned straight
            # after. Readers take the latest row per market, so a repeat is
            # harmless.
            if row.get("result") is not None and (
                    retiring or before is None or before.get("result") is None):
                resolved_rows.append(row)
            by_id[mid] = row
        entry["last_seen_utc"] = fetched_at
        if retiring:
            entry["done"] = True

    if new_rows:
        write_table("poly_markets", new_rows)
        for row in new_rows[:10]:
            log.info("NEW market %s: %r", row.get("market_id"), row.get("question"))
    if resolved_rows:
        from poly_books import resolution_row

        write_table("resolutions", [resolution_row(r, cycle_id, fetched_at) for r in resolved_rows])

    # A retired event has every outcome recorded in `resolutions`; keeping its
    # markets in the cache would only make the book, level and history loops
    # longer every day.
    done_slugs = {s for s, e in tracked.items() if e.get("done")}
    merged = [r for r in by_id.values()
              if not (r.get("event_slug") in done_slugs and r.get("closed"))]
    write_meta("poly_markets_latest", {"cycle_id": cache.get("cycle_id") or cycle_id,
                                       "refreshed_cycle_id": cycle_id,
                                       "refreshed_at_utc": fetched_at,
                                       "markets": merged})
    write_meta(TRACKED_META, {"events": tracked})
    log.info("refreshed %d/%d events: %d new markets, %d newly resolved",
             refreshed, len(slugs), len(new_rows), len(resolved_rows))
    return {"events_refreshed": refreshed, "markets_new": len(new_rows),
            "markets_resolved": len(resolved_rows)}


def should_run_discovery(when, last_run, interval_minutes: int, tick_minutes: int) -> tuple[bool, str]:
    """Same elapsed-time guard as Kalshi: fire when the next tick would overshoot."""
    import datetime as dt

    if interval_minutes <= 0:
        return False, "intraday discovery disabled"
    if last_run is None:
        return True, "no previous discovery"
    elapsed = (when - last_run.astimezone(dt.timezone.utc)).total_seconds() / 60
    if elapsed < 0:
        return True, "last discovery is in the future; clock moved"
    if elapsed >= interval_minutes - tick_minutes / 2:
        return True, f"{elapsed:.0f} min since the last discovery"
    return False, f"only {elapsed:.0f} of {interval_minutes} min since the last discovery"

