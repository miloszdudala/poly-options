"""One-off backfill of closed Polymarket equity events, February-August 2026.

The collector only ever saw markets that were open when it started
(2026-09-16), and every market it has watched close so far resolved YES,
because a touch closes a hit market early while the NO outcomes only arrive at
period end (FINDINGS section 7). Calibration - do contracts priced at 5c
resolve YES 5% of the time - therefore has no sample at all in the live store.
Seven months of it are reachable by slug, which is what this module fetches.

Measured against the live APIs on 2026-09-18:

* Gamma answers `GET /events?slug=...` for closed events. Four slug shapes
  cover the equity ladders:
    monthly hit          what-price-will-{ticker}-hit-in-{month}-2026
    monthly close-above  {ticker}-above-in-{month}-2026
    weekly hit           will-{ticker}-hit-week-of-{month}-{day}-2026
  ({ticker} lower case, {day} the Monday of the week.) The older wording
  ("Will NVIDIA reach $272 in February?", no parenthesised ticker, no "(HIGH)")
  does not match `poly_parse`'s question patterns, but its `groupItemTitle`
  ("↑ $272") does, and the event title carries the ticker - so `market_row`
  still types those markets correctly.

* `prices-history?interval=max` returns an EMPTY history for a token whose
  market has closed - verified on PLTR August tokens, both outcomes, at
  fidelity 1 and 60. The data is still there, but only through an explicit
  `startTs`/`endTs` window, and the window is capped: 15 days is accepted,
  16 days returns `{"error":"invalid filters: 'startTs' and 'endTs' interval
  is too long"}`. `collect_price_history` in `poly_books` therefore cannot
  reach any of this history and this module windows the request itself.

* Only the YES token is fetched. On a market where both were pulled, every
  timestamp present in both series had `YES + NO == 1.0` exactly (max
  deviation 0.0, matching the FINDINGS 3.3 result that a NO ask IS a YES bid),
  and the two series carry the same hourly points stamped a few seconds apart.
  Fetching the NO token would double the request count and add nothing.

Idempotent: a slug that has been fetched, a slug that answered empty, and a
token whose history is stored are all recorded in the `poly_backfill_state`
meta file and skipped on the next run.

    python data/src/poly_backfill.py [--tiers tier1,tier2] [--dry-run]
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from config import ensure_dirs, markets_cfg, underlyings
from storage import read_meta, write_meta, write_table
from util import Http, get_logger, new_cycle_id, parse_iso, utc_iso

log = get_logger("poly.backfill")

STATE_META = "poly_backfill_state"

# Feb 2026 is the first month the September session's MARKETS.md survey reaches
# back to; September is excluded on purpose (see run_calibration).
MONTHS = ["february", "march", "april", "may", "june", "july", "august"]
FIRST_MONDAY = dt.date(2026, 2, 2)
LAST_MONDAY = dt.date(2026, 8, 31)

# Gamma rejects a startTs/endTs span of 16 days; 14 leaves a margin.
HISTORY_WINDOW_DAYS = 14
HISTORY_FIDELITY = 60  # minutes per point


class RateLimited(Exception):
    """A 429. Never retried: the run stops instead."""


# ------------------------------------------------------------------- slugs


def monthly_slugs(ticker: str, month: str) -> list[str]:
    """Both monthly ladder shapes for one ticker and month, hit ladder first."""
    t = ticker.lower()
    return [f"what-price-will-{t}-hit-in-{month}-2026", f"{t}-above-in-{month}-2026"]


def weekly_slug(ticker: str, monday: dt.date) -> str:
    """The weekly hit ladder listed for the week starting on `monday`."""
    month = calendar.month_name[monday.month].lower()
    return f"will-{ticker.lower()}-hit-week-of-{month}-{monday.day}-2026"


def mondays(first: dt.date = FIRST_MONDAY, last: dt.date = LAST_MONDAY) -> list[dt.date]:
    """Every Monday in [first, last]."""
    if first.weekday() != 0:
        first += dt.timedelta(days=(7 - first.weekday()) % 7)
    out = []
    day = first
    while day <= last:
        out.append(day)
        day += dt.timedelta(days=7)
    return out


def all_slugs(tickers: list[str]) -> list[str]:
    """Every slug to probe, monthly ladders before weeklies."""
    out: list[str] = []
    for ticker in tickers:
        for month in MONTHS:
            out += monthly_slugs(ticker, month)
    for ticker in tickers:
        for monday in mondays():
            out.append(weekly_slug(ticker, monday))
    return out


# --------------------------------------------------------- history windows


def history_windows(start: dt.datetime, end: dt.datetime,
                    max_days: int = HISTORY_WINDOW_DAYS) -> list[tuple[int, int]]:
    """Split [start, end] into epoch-second windows Gamma will accept.

    Returned windows tile the span with no gap; the last one may be short. A
    span at or below the cap is one window, so a weekly market costs exactly
    one request and a monthly one three.
    """
    if end <= start:
        end = start + dt.timedelta(hours=1)
    span = dt.timedelta(days=max_days)
    out: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + span, end)
        out.append((int(cursor.timestamp()), int(stop.timestamp())))
        cursor = stop
    return out


def market_life(market: dict) -> tuple[dt.datetime, dt.datetime] | None:
    """When the market could be traded: listing to resolution (or period end).

    An hour of slack each side, because `createdAt` precedes the first quote
    and `closedTime` is the touch, not the last print.
    """
    start = parse_iso(market.get("created_at")) or parse_iso(market.get("start_date"))
    end = parse_iso(market.get("closed_time")) or parse_iso(market.get("end_date"))
    if start is None or end is None:
        return None
    return start - dt.timedelta(hours=1), end + dt.timedelta(hours=1)


# ---------------------------------------------------------------- fetching


class Fetcher:
    """GETs that stop the whole run on a 429 instead of retrying through it.

    `Http` retries a 429 up to five times with backoff, which is the right
    behaviour for a 15-minute collection tick that must come back with
    something. A backfill is different: it is one long burst against a public
    API, and the correct response to being told to slow down is to stop and
    come back, not to push. Transient failures (connection errors, 5xx) still
    use the same backoff ladder and the same shared failure list, so a partial
    run is still visible in the `failures` table.
    """

    def __init__(self, http: Http, workers: int = 8):
        self.http = http
        self.session = http.session
        self.workers = workers
        self.stop = threading.Event()
        cfg = markets_cfg()["http"]
        self.timeout = cfg["timeout_seconds"]
        self.max_retries = cfg["max_retries"]
        self.backoff_base = cfg["backoff_base_seconds"]
        self.backoff_max = cfg["backoff_max_seconds"]
        self.requests_made = 0
        self._lock = threading.Lock()

    def get(self, url: str, params: dict, identifier: str):
        last_err = "unknown"
        for attempt in range(1, self.max_retries + 1):
            if self.stop.is_set():
                return None
            with self._lock:
                self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 429:
                    self.stop.set()
                    raise RateLimited(f"429 on {identifier}")
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        last_err = f"bad json: {exc}"
                elif resp.status_code in (500, 502, 503, 504):
                    last_err = f"HTTP {resp.status_code}"
                else:
                    self._fail(identifier, url, f"HTTP {resp.status_code}")
                    return None
            if attempt < self.max_retries:
                delay = min(self.backoff_base * 2 ** (attempt - 1), self.backoff_max)
                time.sleep(delay * (0.5 + random.random()))
        self._fail(identifier, url, last_err)
        return None

    def _fail(self, identifier, url, error) -> None:
        log.error("FAILED id=%s error=%s url=%s", identifier, error, url)
        self.http.failures.append({"venue": "polymarket", "identifier": str(identifier),
                                   "url": url, "error": str(error),
                                   "failed_at_utc": utc_iso()})

    def map(self, func, items: list) -> list:
        """Run func over items on `workers` threads; a 429 aborts the pool."""
        if not items:
            return []
        results: list = []

        def guarded(item):
            if self.stop.is_set():
                return None
            try:
                return func(item)
            except RateLimited:
                raise
            except Exception as exc:
                log.warning("worker failed on %r: %s", item, exc)
                return None

        with ThreadPoolExecutor(max_workers=min(self.workers, len(items))) as pool:
            futures = [pool.submit(guarded, item) for item in items]
            for future in futures:
                try:
                    results.append(future.result())
                except RateLimited:
                    self.stop.set()
                    results.append(None)
        if self.stop.is_set():
            raise RateLimited("run stopped on HTTP 429")
        return results


def fetch_events(fetcher: Fetcher, base: str, slugs: list[str]) -> dict[str, dict | None]:
    """slug -> event payload, or None where Gamma returned no event."""
    def one(slug: str):
        payload = fetcher.get(f"{base}/events", {"slug": slug}, f"event_{slug}")
        if isinstance(payload, list) and payload:
            return slug, payload[0]
        if isinstance(payload, list):
            return slug, None
        return slug, "error"

    out: dict[str, dict | None] = {}
    for got in fetcher.map(one, slugs):
        if not got:
            continue
        slug, event = got
        if event != "error":
            out[slug] = event
    return out


def fetch_history(fetcher: Fetcher, base: str, token_id: str,
                  windows: list[tuple[int, int]]) -> list[dict]:
    """Every hourly point for one token, one request per window, deduped by hour."""
    points: dict[int, dict] = {}
    for start_ts, end_ts in windows:
        payload = fetcher.get(f"{base}/prices-history",
                              {"market": token_id, "startTs": start_ts,
                               "endTs": end_ts, "fidelity": HISTORY_FIDELITY},
                              f"hist_{token_id[:12]}_{start_ts}")
        if not isinstance(payload, dict):
            continue
        for point in payload.get("history") or []:
            ts = point.get("t")
            if ts is None:
                continue
            # Points land a few seconds either side of the hour; key on the
            # hour so overlapping windows cannot double-count one quote.
            points[int(ts) // 3600] = point
    return [points[k] for k in sorted(points)]


# ------------------------------------------------------------------ the run


def _history_rows(market: dict, token_id: str, points: list[dict],
                  cycle_id: str, fetched_at: str) -> list[dict]:
    rows = []
    for point in points:
        when = dt.datetime.fromtimestamp(int(point["t"]), dt.timezone.utc)
        rows.append({
            "cycle_id": cycle_id,
            "fetched_at_utc": fetched_at,
            "token_id": token_id,
            "market_id": market.get("market_id"),
            "ticker": market.get("ticker"),
            "outcome_index": 0,
            "point_time_utc": when.isoformat(),
            "price": point.get("p"),
            "interval": "backfill_window",
            "fidelity": HISTORY_FIDELITY,
        })
    return rows


def backfill(http: Http, cycle_id: str, tickers: list[str], workers: int = 8,
             dry_run: bool = False) -> dict:
    """Fetch every closed Feb-Aug 2026 ladder for `tickers` and store it."""
    from poly_books import resolution_row
    from poly_discover import market_row

    cfg = markets_cfg()["polymarket"]
    gamma, clob = cfg["gamma_base"], cfg["clob_base"]
    state = read_meta(STATE_META) or {}
    done_slugs = set(state.get("done_slugs") or [])
    empty_slugs = set(state.get("empty_slugs") or [])
    done_tokens = set(state.get("history_tokens") or [])
    known_markets = set(state.get("markets") or [])

    fetcher = Fetcher(http, workers=workers)
    started = time.time()
    counts = {"slugs_probed": 0, "events_found": 0, "slugs_empty": 0,
              "markets_new": 0, "resolutions_new": 0, "history_rows": 0,
              "history_tokens": 0}

    wanted = [s for s in all_slugs(tickers) if s not in done_slugs and s not in empty_slugs]
    log.info("%d slugs to probe (%d already done, %d known empty)",
             len(wanted), len(done_slugs), len(empty_slugs))
    if dry_run:
        return dict(counts, slugs_to_probe=len(wanted))

    events = fetch_events(fetcher, gamma, wanted)
    counts["slugs_probed"] = len(wanted)

    market_rows: list[dict] = []
    fetched_at = utc_iso()
    for slug in wanted:
        if slug not in events:
            continue  # request failed; leave it for the next run
        event = events[slug]
        if event is None:
            empty_slugs.add(slug)
            counts["slugs_empty"] += 1
            log.info("no event for slug %s", slug)
            continue
        counts["events_found"] += 1
        done_slugs.add(slug)
        for market in event.get("markets") or []:
            row = market_row(event, market, cycle_id)
            row["fetched_at_utc"] = fetched_at
            market_rows.append(row)

    fresh = [r for r in market_rows if str(r.get("market_id")) not in known_markets]
    if fresh:
        write_table("poly_markets", fresh)
        counts["markets_new"] = len(fresh)
    closed = [r for r in fresh if r.get("closed")]
    if closed:
        write_table("resolutions", [resolution_row(r, cycle_id, fetched_at) for r in closed])
        counts["resolutions_new"] = len(closed)
    known_markets |= {str(r.get("market_id")) for r in market_rows}

    # Price history, YES token only, for every market not already stored.
    jobs = []
    for row in market_rows:
        token = row.get("token_id_0")
        if not token or str(token) in done_tokens:
            continue
        life = market_life(row)
        if life is None:
            continue
        jobs.append((str(token), row, history_windows(*life)))
    log.info("fetching history for %d tokens (%d windows)",
             len(jobs), sum(len(j[2]) for j in jobs))

    def one_history(job):
        token, row, windows = job
        return token, row, fetch_history(fetcher, clob, token, windows)

    history_rows: list[dict] = []
    try:
        for got in fetcher.map(one_history, jobs):
            if not got:
                continue
            token, row, points = got
            done_tokens.add(token)
            counts["history_tokens"] += 1
            history_rows += _history_rows(row, token, points, cycle_id, fetched_at)
    finally:
        # Whatever was gathered before a 429 is worth keeping, and so is the
        # state that stops the next run from re-fetching it.
        if history_rows:
            write_table("poly_price_history", history_rows)
            counts["history_rows"] = len(history_rows)
        write_meta(STATE_META, {"done_slugs": sorted(done_slugs),
                                "empty_slugs": sorted(empty_slugs),
                                "history_tokens": sorted(done_tokens),
                                "markets": sorted(known_markets),
                                "updated_at_utc": utc_iso()})
    counts["requests"] = fetcher.requests_made
    counts["seconds"] = round(time.time() - started, 1)
    log.info("backfill %s", json.dumps(counts, sort_keys=True))
    return counts


def backfill_hourly_bars(cycle_id: str, tickers: list[str], failures: list) -> int:
    """Hourly regular-session bars, for the spot at each hourly price point.

    `underlying_bars` holds 5 years of daily bars and 60 days of 5-minute bars,
    so for February-August 2026 the only spot available is the previous
    session's close - up to a full session stale at a price point taken at
    15:00 ET, on markets whose barrier is a few percent away. yfinance serves
    ~3 years of 1-hour bars (measured 2026-09-18: PLTR from 2023-10-19,
    5,073 bars), regular session only, which is the same window Pyth candles
    see. Appended under interval='1h'; no schema changes.
    """
    import yfinance as yf

    from underlying import _bar_rows
    from util import yf_pause

    fetched_at = utc_iso()
    rows: list[dict] = []
    for under in underlyings():
        if under.symbol not in tickers:
            continue
        yf_pause()
        try:
            bars = yf.Ticker(under.yf_symbol).history(
                period="730d", interval="1h", auto_adjust=False)
        except Exception as exc:
            failures.append({"venue": "yfinance", "identifier": f"{under.symbol}:1h",
                             "url": f"yf:{under.yf_symbol}", "error": str(exc),
                             "failed_at_utc": utc_iso()})
            continue
        if bars is None or len(bars) == 0:
            continue
        rows += _bar_rows(bars, under.symbol, "1h", cycle_id, fetched_at)
    if rows:
        write_table("underlying_bars", rows)
    log.info("hourly bars: %d rows for %d tickers", len(rows), len(tickers))
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiers", default="tier1,tier2")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-bars", action="store_true")
    args = parser.parse_args(argv)

    ensure_dirs()
    tiers = {t.strip() for t in args.tiers.split(",") if t.strip()}
    tickers = [u.symbol for u in underlyings() if u.tier in tiers]
    log.info("backfilling %d tickers: %s", len(tickers), ", ".join(tickers))

    cycle_id = new_cycle_id()
    http = Http()
    status = "ok"
    try:
        counts = backfill(http, cycle_id, tickers, workers=args.workers,
                          dry_run=args.dry_run)
    except RateLimited as exc:
        log.error("stopped on rate limit: %s", exc)
        counts, status = {"stopped": str(exc)}, "rate_limited"
    if not args.dry_run and not args.skip_bars and status == "ok":
        counts["bar_rows"] = backfill_hourly_bars(cycle_id, tickers, http.failures)

    if http.failures:
        from storage import write_failures
        write_failures(http.failures, cycle_id)
    print(json.dumps({"cycle_id": cycle_id, "status": status, **counts}, sort_keys=True))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
