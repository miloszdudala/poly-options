"""Run entrypoints. Each job is short-lived: fetch -> write -> exit.

    python jobs.py snapshot   # prediction-market books (+ options in US hours)
    python jobs.py daily      # metadata, rates, dividends, earnings, bars, history
    python jobs.py discover   # discovery only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback

from config import ensure_dirs, markets_cfg, underlyings
from storage import read_meta, write_failures, write_meta, write_run_summary
from util import (
    Http, get_logger, is_regular_hours, is_trading_day, new_cycle_id, now_utc,
    parse_iso, should_run_kalshi, should_run_snapshot, steady_seconds, utc_iso,
)

log = get_logger("jobs")


class Run:
    """Times a job and always writes a summary row, even on failure."""

    def __init__(self, job: str, deadline_seconds: float | None = None):
        self.job = job
        self.cycle_id = new_cycle_id()
        self.started = now_utc()
        self.counts: dict = {}
        # Only the snapshot is deadlined. The daily job legitimately runs ~17
        # minutes and has no next tick to protect.
        # steady_seconds(), not time.monotonic(): the deadline protects the
        # next wall-clock tick, and time.monotonic() stops during system sleep.
        self.deadline = (steady_seconds() + deadline_seconds) if deadline_seconds else None
        self.http = Http(deadline=self.deadline)
        self.status = "ok"
        self.notes: list[str] = []

    def out_of_time(self) -> bool:
        return self.deadline is not None and steady_seconds() >= self.deadline

    def note(self, text: str) -> None:
        log.info(text)
        self.notes.append(text)

    def finish(self, status: str | None = None) -> None:
        ended = now_utc()
        if status:
            self.status = status
        write_failures(self.http.failures, self.cycle_id)
        row = {
            "cycle_id": self.cycle_id,
            "fetched_at_utc": utc_iso(ended),
            "job": self.job,
            "started_at_utc": utc_iso(self.started),
            "ended_at_utc": utc_iso(ended),
            "duration_seconds": (ended - self.started).total_seconds(),
            "status": self.status,
            "counts_json": json.dumps(self.counts, sort_keys=True),
            "failure_count": len(self.http.failures),
            "notes": "; ".join(self.notes) or None,
        }
        write_run_summary(row)
        write_meta("last_tick", {"at_utc": utc_iso(ended), "job": self.job,
                                 "ran": True, "reason": self.status})
        log.info("RUN %s %s in %.1fs counts=%s failures=%d",
                 self.job, self.status, row["duration_seconds"],
                 row["counts_json"], row["failure_count"])


def _markets_for_books(run: Run) -> list[dict]:
    """Use cached discovery when it exists; otherwise discover first."""
    from poly_discover import discover

    cached = read_meta("poly_markets_latest")
    if cached and cached.get("markets"):
        return cached["markets"]
    run.note("no cached discovery, running discovery first")
    markets, _complete = discover(run.http, run.cycle_id)
    return markets


def job_discover(run: Run) -> None:
    from poly_discover import discover

    markets, complete = discover(run.http, run.cycle_id)
    run.counts["poly_markets"] = len(markets)
    if complete:
        write_meta("last_discovery", {"at_utc": utc_iso(run.started), "cycle_id": run.cycle_id})
    else:
        run.status = "truncated"


def job_snapshot(run: Run) -> None:
    """Books at every cadence; options only during US regular hours."""
    from kalshi import collect
    from options import collect_options
    from poly_books import collect_books

    from poly_discover import tracked_markets
    from poly_events import refresh_events

    markets = _markets_for_books(run)

    # Polymarket lists new barrier levels around 12:40 ET; a book that is never
    # requested is a market that never existed to the model. Every known event
    # is refreshed by slug on every tick (cheap: ~140 concurrent GETs); brand
    # new events come from the full walk in _intraday_discovery(), last.
    run.counts.update(refresh_events(run.http, run.cycle_id))
    markets = tracked_markets() or markets

    book_stats, _books = collect_books(run.http, markets, run.cycle_id)
    run.counts.update(book_stats)

    # Each phase writes its own table before the next starts, so abandoning the
    # rest of the run keeps everything already collected.
    if run.out_of_time():
        run.note("deadline hit after polymarket books: kalshi and options skipped")
        run.status = "truncated"
        return

    # The second read of the same books, ~30 s on, before anything slower.
    # Options took 110 s on 2026-10-16 17:00Z, which put a recheck placed after
    # them two minutes out: longer than a trader needs to act.
    _recheck_books(run, markets, steady_seconds())

    if run.out_of_time():
        run.note("deadline hit after the book recheck: options and kalshi skipped")
        run.status = "truncated"
        return

    # Options before the hourly Kalshi pass (~90 s), so the chain lands as
    # close to the books it is compared with as it can.
    if is_regular_hours():
        run.counts.update(collect_options(run.http, underlyings(with_options_only=True),
                                          run.cycle_id))
    else:
        run.note("outside US regular hours: options skipped")

    if run.out_of_time():
        run.note("deadline hit after options: kalshi skipped")
        run.status = "truncated"
        return

    last_kalshi = (read_meta("last_kalshi") or {}).get("at_utc")
    ok, why = should_run_kalshi(run.started, parse_iso(last_kalshi))
    if ok:
        run.counts.update(collect(run.http, run.cycle_id, with_books=True))
        write_meta("last_kalshi", {"at_utc": utc_iso(run.started),
                                   "cycle_id": run.cycle_id})
    else:
        run.note(f"kalshi cadence: {why}")

    _intraday_discovery(run)


def _pause(seconds: float) -> None:
    """`time.sleep`, behind a name the tests can stub without stubbing it for
    every module in the process."""
    time.sleep(seconds)


def _recheck_books(run: Run, markets: list[dict], books_done: float) -> None:
    """Read every Polymarket book again into `poly_books_recheck`, at least
    `recheck_books_after_seconds` after the first read finished.

    The replay fills against the first read. A live trader sees the same book,
    prices it and sends an order, which takes seconds; the recheck says which
    levels were still there by then. Before this table the only check was the
    next snapshot, 15 minutes on, which is far longer than a trader needs and
    counts a level as lost when its poster merely re-quoted it.
    """
    from poly_books import collect_books

    delay = float(markets_cfg()["schedule"].get("recheck_books_after_seconds", 0) or 0)
    if delay <= 0:
        return
    wait = delay - (steady_seconds() - books_done)
    if wait > 0:
        _pause(wait)
    stats, _ = collect_books(run.http, markets, run.cycle_id, table="poly_books_recheck")
    run.counts.update({f"recheck_{k}": v for k, v in stats.items()})


def _intraday_discovery(run: Run) -> None:
    """Full walk of open events, in whatever budget the tick has left.

    It runs after books, Kalshi and options, never before them. Before
    2026-09-23 it ran first, and at the US open Gamma serves the walk at ~5 s a
    page: the 13:30 UTC walk ate the whole 600 s budget on 2026-09-21 and
    2026-09-22, and every opening book request failed with "run deadline
    exceeded". A walk the budget cannot finish saves its cursor and resumes on
    the next tick; last_discovery is stamped only when a walk completes, so an
    unfinished one keeps being due. New markets it finds are booked from the
    following tick.
    """
    from poly_discover import discover
    from poly_events import should_run_discovery

    disc_cfg = markets_cfg()["polymarket"]["discovery"]
    last_disc = (read_meta("last_discovery") or {}).get("at_utc")
    ok, why = should_run_discovery(
        run.started, parse_iso(last_disc),
        int(disc_cfg.get("intraday_interval_minutes", 0) or 0),
        int(markets_cfg()["schedule"]["intraday_interval_minutes"]))
    if not ok:
        return
    if run.out_of_time():
        run.note(f"intraday discovery due ({why}) but no budget left: next tick")
        return
    run.note(f"intraday discovery: {why}")
    markets, complete = discover(run.http, run.cycle_id)
    if complete:
        run.counts["poly_markets"] = len(markets)
        write_meta("last_discovery", {"at_utc": utc_iso(run.started), "cycle_id": run.cycle_id})
    else:
        # Deliberately not "truncated": this tick's books, Kalshi and options
        # are all written, and runs.status is what says a cycle is usable.
        run.note("intraday discovery unfinished: resumes next tick")


def job_daily(run: Run) -> None:
    """Post-close refresh of everything slow-moving."""
    import kalshi
    from poly_books import collect_price_history, collect_resolutions
    from poly_discover import discover
    from options import collect_options
    from reference import collect_rates
    from underlying import collect_bars, collect_dividends_and_earnings, futures_symbols

    from poly_discover import tracked_markets
    from poly_events import refresh_events

    markets, complete = discover(run.http, run.cycle_id)
    run.counts["poly_markets"] = len(markets)
    if complete:
        write_meta("last_discovery", {"at_utc": utc_iso(run.started), "cycle_id": run.cycle_id})
    else:
        # Left due, so the next session's first tick resumes the walk.
        run.note("discovery unfinished: resumes on the next snapshot tick")
    # Events that have closed no longer appear in the open-events walk; this
    # is where their final outcomes (every NO) get written.
    run.counts.update(refresh_events(run.http, run.cycle_id))
    markets = tracked_markets() or markets
    run.counts["resolutions"] = collect_resolutions(run.http, markets, run.cycle_id)
    run.counts["kalshi_resolutions"] = kalshi.collect_resolutions(run.http, run.cycle_id)
    run.counts["poly_price_history"] = collect_price_history(
        run.http, markets, run.cycle_id)

    # Daily yfinance pass: an independent cross-check on Cboe quotes, kept off
    # the 15-minute path where it cost ~406 s of a 15-minute budget.
    run.counts.update({f"xcheck_{k}": v for k, v in collect_options(
        run.http, underlyings(with_options_only=True), run.cycle_id,
        include_yfinance=True).items()})

    run.counts.update(collect_rates(run.http, run.cycle_id))

    symbols = [(u.symbol, u.yf_symbol) for u in underlyings()] + futures_symbols()
    run.counts.update(collect_bars(symbols, run.cycle_id, run.http.failures))
    run.counts.update(collect_dividends_and_earnings(
        [(u.symbol, u.yf_symbol) for u in underlyings()],
        run.cycle_id, run.http.failures))


JOBS = {"snapshot": job_snapshot, "daily": job_daily, "discover": job_discover}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="poly-options collector")
    parser.add_argument("job", choices=sorted(JOBS))
    parser.add_argument("--force", action="store_true",
                        help="run even on a non-trading day")
    args = parser.parse_args(argv)

    ensure_dirs()
    deadline = None
    if args.job == "snapshot":
        deadline = markets_cfg()["schedule"].get("snapshot_deadline_seconds") or None
    run = Run(args.job, deadline_seconds=deadline)

    if args.job == "daily" and not is_trading_day() and not args.force:
        run.note("not a US trading day: skipping")
        run.finish("skipped")
        return 0

    if args.job == "snapshot" and not args.force:
        ok, why = should_run_snapshot()
        if not ok:
            # A skipped tick is a heartbeat, not a run. launchd fires every 15
            # minutes around the clock but only the NYSE session is collected,
            # so ~70 of the ~96 daily ticks skip - writing a `runs` row for each
            # would add ~25k single-row Parquet files a year to say nothing
            # happened. One overwritten meta file records the same fact, and it
            # is the thing to check to prove the machine was awake.
            log.info("cadence guard: %s", why)
            write_meta("last_tick", {"at_utc": utc_iso(), "job": args.job,
                                     "ran": False, "reason": why})
            return 0

    try:
        JOBS[args.job](run)
    except Exception:
        log.error("job %s crashed:\n%s", args.job, traceback.format_exc())
        run.finish("error")
        return 1
    run.finish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
