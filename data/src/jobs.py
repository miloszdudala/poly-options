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
    should_run_kalshi, should_run_snapshot, utc_iso,
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
        self.deadline = (time.monotonic() + deadline_seconds) if deadline_seconds else None
        self.http = Http(deadline=self.deadline)
        self.status = "ok"
        self.notes: list[str] = []

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

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
    return discover(run.http, run.cycle_id)


def job_discover(run: Run) -> None:
    from poly_discover import discover

    markets = discover(run.http, run.cycle_id)
    run.counts["poly_markets"] = len(markets)


def job_snapshot(run: Run) -> None:
    """Books at every cadence; options only during US regular hours."""
    from kalshi import collect
    from options import collect_options
    from poly_books import collect_books

    markets = _markets_for_books(run)
    book_stats, _books = collect_books(run.http, markets, run.cycle_id)
    run.counts.update(book_stats)

    # Each phase writes its own table before the next starts, so abandoning the
    # rest of the run keeps everything already collected.
    if run.out_of_time():
        run.note("deadline hit after polymarket books: kalshi and options skipped")
        run.status = "truncated"
        return

    if should_run_kalshi()[0]:
        run.counts.update(collect(run.http, run.cycle_id, with_books=True))
    else:
        run.note("kalshi cadence: skipped this tick")

    if run.out_of_time():
        run.note("deadline hit after kalshi: options skipped")
        run.status = "truncated"
        return

    if is_regular_hours():
        run.counts.update(collect_options(run.http, underlyings(with_options_only=True),
                                          run.cycle_id))
    else:
        run.note("outside US regular hours: options skipped")


def job_daily(run: Run) -> None:
    """Post-close refresh of everything slow-moving."""
    from poly_books import collect_price_history, collect_resolutions
    from poly_discover import discover
    from options import collect_options
    from reference import collect_rates
    from underlying import collect_bars, collect_dividends_and_earnings, futures_symbols

    markets = discover(run.http, run.cycle_id)
    run.counts["poly_markets"] = len(markets)
    run.counts["resolutions"] = collect_resolutions(run.http, markets, run.cycle_id)
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
