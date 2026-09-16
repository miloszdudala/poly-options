"""HTTP with retry/backoff, time helpers, market calendar, logging, cycle ids."""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
import uuid
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from config import LOG_DIR, markets_cfg

UTC = dt.timezone.utc

_log_configured = False


def get_logger(name: str) -> logging.Logger:
    global _log_configured
    if not _log_configured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        fh = logging.FileHandler(LOG_DIR / f"collector-{today_utc()}.log")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
        _log_configured = True
    return logging.getLogger(name)


# ---------------------------------------------------------------- time helpers


def now_utc() -> dt.datetime:
    """Timezone-aware current UTC time."""
    return dt.datetime.now(UTC)


def utc_iso(when: dt.datetime | None = None) -> str:
    """RFC3339 UTC string, always with an explicit +00:00 offset."""
    when = when or now_utc()
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(UTC).isoformat()


def today_utc() -> str:
    return now_utc().strftime("%Y-%m-%d")


def market_tz() -> ZoneInfo:
    """US market timezone from the tz database - never a hard-coded offset."""
    return ZoneInfo(markets_cfg()["schedule"]["market_tz"])


def now_market() -> dt.datetime:
    return now_utc().astimezone(market_tz())


def parse_epoch(value: Any) -> dt.datetime | None:
    """Parse an epoch in seconds, milliseconds or microseconds into aware UTC."""
    if value in (None, "", "0"):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    # Disambiguate by magnitude: ~1.8e9 s, ~1.8e12 ms, ~1.8e15 us.
    if n > 1e17:
        n /= 1e9  # nanoseconds
    elif n > 1e14:
        n /= 1e6
    elif n > 1e11:
        n /= 1e3
    return dt.datetime.fromtimestamp(n, tz=UTC)


def parse_iso(value: Any, assume_tz: dt.tzinfo | None = None) -> dt.datetime | None:
    """Parse an ISO-8601 string to aware UTC.

    Naive strings are interpreted in `assume_tz` (used for sources such as Cboe
    that publish naive ET timestamps) and default to UTC otherwise.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=assume_tz or UTC)
    return parsed.astimezone(UTC)


def new_cycle_id(when: dt.datetime | None = None) -> str:
    """Sortable id shared by every row written in one run."""
    when = when or now_utc()
    return when.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


# ------------------------------------------------------------ market calendar


def _calendar():
    import pandas_market_calendars as mcal

    return mcal.get_calendar("NYSE")


def is_trading_day(day: dt.date | None = None) -> bool:
    """True if `day` is an NYSE session (skips weekends and US market holidays)."""
    day = day or now_market().date()
    sched = _calendar().schedule(start_date=str(day), end_date=str(day))
    return len(sched) > 0


def is_regular_hours(when: dt.datetime | None = None) -> bool:
    """True during the NYSE regular session, honouring half-days and DST."""
    when = (when or now_utc()).astimezone(UTC)
    local_day = when.astimezone(market_tz()).date()
    sched = _calendar().schedule(start_date=str(local_day), end_date=str(local_day))
    if len(sched) == 0:
        return False
    open_ts = sched.iloc[0]["market_open"].to_pydatetime().astimezone(UTC)
    close_ts = sched.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    return open_ts <= when <= close_ts


def should_run_snapshot(when: dt.datetime | None = None) -> tuple[bool, str]:
    """Cadence guard for the snapshot job.

    launchd fires every 15 minutes around the clock; the spec wants 15-minute
    snapshots during the NYSE regular session and hourly ones outside it, so the
    off-hours ticks that are not on the hour are skipped here rather than by
    maintaining several launchd entries that would need re-tuning at every DST
    change.
    """
    when = (when or now_utc()).astimezone(UTC)
    if is_regular_hours(when):
        return True, "regular hours"
    interval = markets_cfg()["schedule"]["intraday_interval_minutes"]
    if when.astimezone(market_tz()).minute < interval:
        return True, "off-hours hourly tick"
    return False, "off-hours, not the hourly tick"


def should_run_kalshi(when: dt.datetime | None = None) -> tuple[bool, str]:
    """Cadence guard for Kalshi, which the project treats as a secondary track.

    Kalshi only exposes one order book per request, so all ~3.5k markets cost
    ~140 s - most of a snapshot's runtime. Its own interval is configurable so it
    can be thinned without touching the Polymarket cadence.
    """
    when = (when or now_utc()).astimezone(UTC)
    interval = markets_cfg()["kalshi"].get("interval_minutes", 15)
    if interval <= 15:
        return True, "every tick"
    minute = when.astimezone(market_tz()).minute
    if minute % interval < 15:
        return True, f"on the {interval}-minute boundary"
    return False, f"not a {interval}-minute boundary"


# ------------------------------------------------------------------ http layer


class HttpError(Exception):
    pass


class Http:
    """requests session with exponential backoff + jitter and failure logging."""

    def __init__(self, failures: list | None = None):
        cfg = markets_cfg()["http"]
        self.timeout = cfg["timeout_seconds"]
        self.max_retries = cfg["max_retries"]
        self.backoff_base = cfg["backoff_base_seconds"]
        self.backoff_max = cfg["backoff_max_seconds"]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": cfg["user_agent"]})
        self.log = get_logger("http")
        # Shared list so a run can report every failed request at the end.
        self.failures = failures if failures is not None else []

    def request(
        self,
        method: str,
        url: str,
        *,
        venue: str,
        identifier: str,
        expect_json: bool = True,
        **kwargs,
    ):
        """Perform a request, retrying transient failures. Returns None on give-up."""
        kwargs.setdefault("timeout", self.timeout)
        last_err: str | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    if not expect_json:
                        return resp.content
                    try:
                        return resp.json()
                    except ValueError as exc:
                        last_err = f"bad json: {exc}"
                elif resp.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {resp.status_code}"
                else:
                    # 4xx other than 429: not worth retrying.
                    self._record_failure(
                        venue, identifier, url, f"HTTP {resp.status_code}"
                    )
                    return None
            if attempt < self.max_retries:
                delay = min(self.backoff_base * 2 ** (attempt - 1), self.backoff_max)
                time.sleep(delay * (0.5 + random.random()))  # full-ish jitter
        self._record_failure(venue, identifier, url, last_err or "unknown")
        return None

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def _record_failure(self, venue, identifier, url, error) -> None:
        self.log.error("FAILED venue=%s id=%s error=%s url=%s", venue, identifier, error, url)
        self.failures.append(
            {
                "venue": venue,
                "identifier": str(identifier),
                "url": url,
                "error": str(error),
                "failed_at_utc": utc_iso(),
            }
        )


def yf_pause() -> None:
    """Randomised pause between yfinance calls."""
    cfg = markets_cfg()["http"]
    time.sleep(random.uniform(cfg["yf_pause_min"], cfg["yf_pause_max"]))


def parallel_map(func, items: list, workers: int = 8) -> list:
    """Run func over items with a small thread pool, preserving input order.

    Used where a venue only offers per-item endpoints (Kalshi order books) and a
    sequential loop would not fit inside the 15-minute cadence. Each worker gets
    its own requests.Session via the Http instance passed in by the caller;
    exceptions are returned in place rather than killing the pool.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not items:
        return []
    workers = max(1, min(workers, len(items)))

    def guarded(item):
        try:
            return func(item)
        except Exception as exc:  # one bad item must not stop the run
            get_logger("parallel").warning("worker failed on %r: %s", item, exc)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(guarded, items))


def chunked(items: Iterable, size: int) -> Iterable[list]:
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
