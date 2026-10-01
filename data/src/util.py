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


def steady_seconds() -> float:
    """Monotonic seconds that keep counting while the machine is asleep.

    `time.monotonic()` is mach_absolute_time() on macOS and CLOCK_MONOTONIC on
    Linux; neither advances across a system suspend. Every deadline in this
    collector is there to protect the *next* tick, which is a wall-clock event,
    so budgeting in awake-seconds is simply the wrong unit on a laptop.

    MEASURED 2026-09-18 on the collecting Mac: 31.3 of the 61.4 hours since
    boot were missing from time.monotonic(). The cost was not theoretical - the
    four snapshots of the 2026-09-17 session ran 49, 104, 126 and 57 minutes of
    wall clock against a 600 s deadline without ever tripping it, because a
    closed clamshell only dark-wakes for a few seconds at a time. The 15:45 ET
    tick was still writing books at 16:41 ET, after the close.

    CLOCK_BOOTTIME (Linux) and CLOCK_MONOTONIC (Darwin) both include suspend.
    Unlike time.time() neither can be stepped backwards by NTP.
    """
    for name in ("CLOCK_BOOTTIME", "CLOCK_MONOTONIC"):
        clock = getattr(time, name, None)
        if clock is None:
            continue
        try:
            return time.clock_gettime(clock)
        except (OSError, ValueError):  # pragma: no cover - platform dependent
            continue
    return time.monotonic()  # pragma: no cover - non-POSIX fallback


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

    launchd fires every 15 minutes around the clock; the cadence is decided here
    rather than by maintaining several launchd entries that would need re-tuning
    at every DST change.

    By default nothing is collected outside the NYSE regular session. The books
    do move overnight and at weekends, but there is no option chain quoted then
    to compare them against, which is the only reason this dataset exists. It
    also means the machine only has to be awake during the session.
    """
    when = (when or now_utc()).astimezone(UTC)
    if is_regular_hours(when):
        return True, "regular hours"
    cfg = markets_cfg()["schedule"]
    if not cfg.get("collect_offhours", False):
        return False, "outside the NYSE session"
    if when.astimezone(market_tz()).minute < cfg["intraday_interval_minutes"]:
        return True, "off-hours hourly tick"
    return False, "off-hours, not the hourly tick"


def should_run_kalshi(when: dt.datetime | None = None,
                      last_run: dt.datetime | None = None) -> tuple[bool, str]:
    """Cadence guard for Kalshi.

    Kalshi only exposes one order book per request, so fetching depth for all
    ~3.5k open markets cost ~140 s - most of a snapshot's runtime, which is why
    this guard exists at all. Now that depth is only requested for markets that
    could actually be traded (kalshi.book_filter), the interval is back to every
    tick; it stays configurable so Kalshi can be thinned again without touching
    the Polymarket cadence.

    The interval is measured from the last collection, not from the minute of
    the hour. The earlier `minute % interval < 15` test assumed ticks land on
    the 15-minute grid, which is exactly what a laptop does not do: launchd
    fires on the next dark wake, so the 2026-09-17 session ticked at ET minutes
    18, 31, 38 and 41 and the hourly guard rejected all four. A whole session
    of Kalshi books was lost with nothing in the log but "skipped this tick".
    """
    when = (when or now_utc()).astimezone(UTC)
    interval = markets_cfg()["kalshi"].get("interval_minutes", 15)
    tick = markets_cfg()["schedule"]["intraday_interval_minutes"]
    if interval <= tick:
        return True, "every tick"
    if last_run is None:
        return True, "no previous kalshi collection"
    elapsed = (when - last_run.astimezone(UTC)).total_seconds() / 60
    if elapsed < 0:
        return True, "last collection is in the future; clock moved"
    # Fire when the next tick would overshoot the interval by more than it
    # undershoots, so drifting ticks still average out at one per interval
    # instead of skipping to the one after.
    if elapsed >= interval - tick / 2:
        return True, f"{elapsed:.0f} min since the last collection"
    return False, f"only {elapsed:.0f} of {interval} min since the last collection"


# ------------------------------------------------------------------ http layer


class HttpError(Exception):
    pass


class Http:
    """requests session with exponential backoff + jitter and failure logging."""

    def __init__(self, failures: list | None = None, deadline: float | None = None):
        # deadline is a steady_seconds() stamp past which we stop trying. It is
        # checked before every attempt and bounds every backoff sleep, so a run
        # cannot be dragged past its budget by the retry ladder alone. It must
        # be steady_seconds(), not time.monotonic(): see that function.
        self.deadline = deadline
        cfg = markets_cfg()["http"]
        self.timeout = cfg["timeout_seconds"]
        self.max_retries = cfg["max_retries"]
        self.backoff_base = cfg["backoff_base_seconds"]
        self.backoff_max = cfg["backoff_max_seconds"]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": cfg["user_agent"]})
        # urllib3 defaults to 10 pooled connections per host. The Kalshi book
        # sweep runs more threads than that, and once the pool is full urllib3
        # discards the connection after each response - so every request past
        # the tenth pays a fresh TCP + TLS handshake. Size the pool to the
        # widest thread pool we run so keep-alive actually holds.
        pool = max(10, 2 * int(markets_cfg()["kalshi"].get("book_workers", 8)))
        adapter = requests.adapters.HTTPAdapter(pool_connections=pool,
                                                pool_maxsize=pool)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
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
            if self.past_deadline():
                self._record_failure(venue, identifier, url, "run deadline exceeded")
                return None
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
                delay *= 0.5 + random.random()  # full-ish jitter
                if self.deadline is not None:
                    delay = min(delay, max(0.0, self.deadline - steady_seconds()))
                time.sleep(delay)
        self._record_failure(venue, identifier, url, last_err or "unknown")
        return None

    def past_deadline(self) -> bool:
        return self.deadline is not None and steady_seconds() >= self.deadline

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
    sequential loop would not fit inside the 15-minute cadence. Workers SHARE the
    caller's Http, and so its one requests.Session: that is deliberate, because a
    shared session is what lets connections be reused across threads, but it is
    why Http sizes its connection pool from book_workers. Exceptions are returned
    in place rather than killing the pool.
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
