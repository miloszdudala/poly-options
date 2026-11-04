"""The only module in `model/` that reads Parquet.

Everything else takes frames. That is not tidiness: the traps in this dataset
are all at the read boundary (MODELS section 6), and a trap fixed in one place
stays fixed.

The traps this module owns:

* **`source_quote_time_utc` is the file's generation time, not the quote time.**
  The quote time is `max(last_trade_time_utc)` per symbol per cycle. Measured
  2026-09-16: a 15:53Z fetch carried a 15:38Z latest print. Anything comparing
  an option-implied probability against a live book without that 15 minutes is
  mostly measuring underlying drift (README).
* **`(ticker, strike, contract_type)` is not unique** across expiries. Key on
  `market_id`, always.
* **`depth_rank = 0` is top of book; `level_rank = 0` is the worst price**, the
  CLOB returns both sides worst-first.
* **The session filter is 09:30-16:00 New York time** and **2026-09-17 is
  dropped**: the laptop slept and its four cycles are truncated, one of them
  gathered over 126 minutes. `session_cycles` also drops any cycle whose run
  was truncated or took longer than one cadence.
* **Latest row per key.** `poly_markets` and `resolutions` are append-only and
  rewritten in full on every daily run.
"""
from __future__ import annotations

import datetime as dt
import functools
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
# APPENDED, never inserted at the front: `data/src` also contains a module
# called `schemas`, and putting it ahead of `model/` makes `import schemas`
# resolve to the collector's table definitions instead of the engine's - which
# fails at the one call site that needs the difference, after a full run.
if str(HERE.parent / "data" / "src") not in sys.path:
    sys.path.append(str(HERE.parent / "data" / "src"))

from config import PARQUET_DIR  # noqa: E402

EASTERN = "America/New_York"

# MODELS section 6. Both bounds are inclusive of the cycle's start stamp, and
# are New York wall-clock times. They were 13:30Z-20:00Z until 2026-10-17,
# which is right only during US daylight saving: from 1 November it would
# have dropped the session's last hour and kept an hour before the open.
SESSION_OPEN_ET = (9, 30)
SESSION_CLOSE_ET = (16, 0)
EXCLUDED_DATES = frozenset({"2026-09-17"})


def _src(table: str) -> str:
    root = PARQUET_DIR / table
    return f"read_parquet('{root}/**/*.parquet', hive_partitioning=true, union_by_name=true)"


@functools.lru_cache(maxsize=1)
def _con():
    import duckdb

    return duckdb.connect()


def _q(sql: str, tries: int = 8) -> pd.DataFrame:
    """Run a query, waiting out a Parquet file the collector is still writing.

    The collector writes a file every 15 minutes; a glob that lands mid-write
    sees a file with no footer yet and DuckDB refuses the whole query
    (run_all crashed on one on 2026-10-19). A complete file appears within
    seconds, so a short wait and a re-read is enough.
    """
    import duckdb

    for attempt in range(tries):
        try:
            return _con().sql(sql).df()
        except duckdb.InvalidInputException:
            if attempt == tries - 1:
                raise
            time.sleep(10)


def cycle_date(cycle_id: str) -> str:
    """The UTC date a cycle started, read out of the cycle_id itself."""
    return f"{cycle_id[:4]}-{cycle_id[4:6]}-{cycle_id[6:8]}"


def cycle_start(cycle_id: str) -> dt.datetime:
    return dt.datetime.strptime(cycle_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def in_session(cycle_id: str) -> bool:
    """09:30-16:00 New York time on a date that is not on the excluded list."""
    if cycle_date(cycle_id) in EXCLUDED_DATES:
        return False
    from zoneinfo import ZoneInfo

    local = cycle_start(cycle_id).astimezone(ZoneInfo(EASTERN))
    clock = (local.hour, local.minute, local.second)
    return (*SESSION_OPEN_ET, 0) <= clock <= (*SESSION_CLOSE_ET, 0)


# A run longer than one 15-minute cadence was gathered across sleep or a retry
# ladder: its books are spread over more time than the cycle label claims.
MAX_CYCLE_SECONDS = 900


def session_cycles(date: str) -> list[str]:
    """Every cycle of `date` that has books, falls inside the session, and whose
    run finished cleanly.

    A truncated run can still have written books: 2026-09-21 15:41Z wrote a
    full Polymarket sweep during dark wakes over 950 s, then ran out of budget
    before options, so there is nothing to price it against. Cycles with no
    run row at all (two hand runs on 2026-09-16) are kept.
    """
    rows = _q(f"""
        select distinct b.cycle_id from {_src('poly_books')} b
        where b.date = '{date}'
          and not exists (select 1 from {_src('runs')} r
                          where r.cycle_id = b.cycle_id
                            and (r.status <> 'ok' or r.duration_seconds > {MAX_CYCLE_SECONDS}))
        order by b.cycle_id
    """)
    return [c for c in rows["cycle_id"].tolist() if in_session(c)]


# ------------------------------------------------------------------- books


def books(cycle_id: str, contract_types: tuple[str, ...] = ("hit_up", "hit_down")) -> pd.DataFrame:
    """Every level of every book in one cycle, one row per level.

    `depth_rank` ascending is best price first on both sides. Markets already
    closed or no longer accepting orders never reach here - the collector drops
    them before requesting a book.
    """
    types = ", ".join(f"'{t}'" for t in contract_types)
    df = _q(f"""
        select cycle_id, fetched_at_utc, book_timestamp_utc, market_id, token_id,
               ticker, contract_type, barrier, strike, end_date, period,
               outcome, outcome_index, side, depth_rank, price, size
        from {_src('poly_books')}
        where cycle_id = '{cycle_id}' and contract_type in ({types})
        order by market_id, outcome_index, side, depth_rank
    """)
    for col in ("book_timestamp_utc", "fetched_at_utc", "end_date"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", utc=True, errors="coerce")
    return df


def market_book_times(cycle_id: str) -> pd.DataFrame:
    """One row per market: its book timestamp, fetch time, and the age between.

    `book_timestamp_utc` is the venue's *last change* to that book, not our
    snapshot time, so a dead ladder can carry a timestamp days old (measured
    2026-09-16: median 15 s, p90 165 s, max 4.9 days). `book_age_s` is how the
    engine tells a live quote from a fossil.
    """
    df = _q(f"""
        select market_id, ticker, contract_type, barrier, end_date, period,
               max(book_timestamp_utc) book_timestamp_utc,
               max(fetched_at_utc) fetched_at_utc
        from {_src('poly_books')}
        where cycle_id = '{cycle_id}' and contract_type in ('hit_up', 'hit_down')
        group by 1, 2, 3, 4, 5, 6
    """)
    for col in ("book_timestamp_utc", "fetched_at_utc", "end_date"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", utc=True, errors="coerce")
    df["book_age_s"] = (df["fetched_at_utc"] - df["book_timestamp_utc"]).dt.total_seconds()
    return df


# ---------------------------------------------------------- option chain


def option_chain(cycle_id: str, symbols: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Cboe rows for one cycle, filtered to contracts worth believing.

    FINDINGS section 4: 49% of Cboe contracts never trade on a given day and
    their published IV comes from a placeholder quote - two vendors disagreed by
    59 vol points at p90 on those against 2 points on liquid ones. MODELS 3.2's
    filter is `open_interest >= 100 or volume > 0`, and `bid > 0`.

    yfinance rows are never returned: its IV runs 9-11 vol points low on
    identical bid/ask (FINDINGS section 4), and this engine recomputes IV
    anyway, so the only thing yfinance could contribute is a worse quote.
    """
    where = ""
    if symbols:
        joined = ", ".join(f"'{s}'" for s in symbols)
        where = f"and underlying in ({joined})"
    df = _q(f"""
        select underlying, contract_symbol, option_type, expiry, strike,
               bid, ask, last_price, last_trade_time_utc, volume, open_interest,
               source_iv, source_delta
        from {_src('option_quotes')}
        where cycle_id = '{cycle_id}' and source = 'cboe'
          and bid > 0 and (open_interest >= 100 or volume > 0) {where}
    """)
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
    df["last_trade_time_utc"] = pd.to_datetime(df["last_trade_time_utc"],
                                               format="ISO8601", utc=True, errors="coerce")
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    return df


def quote_times(cycle_id: str) -> pd.Series:
    """`max(last_trade_time_utc)` per symbol: the real quote time of the chain.

    Never `source_quote_time_utc`, which is when Cboe generated the file - about
    our fetch time, and 15 minutes after the newest print inside it.
    """
    df = _q(f"""
        select underlying, max(last_trade_time_utc) quote_time
        from {_src('option_quotes')}
        where cycle_id = '{cycle_id}' and source = 'cboe' and bid > 0
        group by 1
    """)
    stamps = pd.to_datetime(df["quote_time"], format="ISO8601", utc=True, errors="coerce")
    return pd.Series(stamps.tolist(), index=df["underlying"].tolist(), dtype="object")


def cboe_spot(cycle_id: str) -> pd.DataFrame:
    """Cboe's own underlying quote for the cycle: the fallback spot, 15 min old."""
    df = _q(f"""
        select symbol, last_price, iv30 from {_src('underlying_quotes')}
        where cycle_id = '{cycle_id}' and source = 'cboe'
    """)
    return df


# ------------------------------------------------------------------- bars


@functools.lru_cache(maxsize=64)
def daily_bars(symbol: str) -> pd.DataFrame:
    """One row per session, sorted, newest write wins on a duplicated stamp.

    The dedupe is not tidiness. P1's backfill appended 1-hour bars beside the
    collector's daily ones, and `select distinct` keeps both rows whenever
    their closes differ in the last decimal - which they do, on all 19 equity
    tickers. A duplicated session is a spurious `ln(C/C) = 0` return in the
    EWMA volatility and a spurious same-day "gap" in `f`, so it has to go
    before anything derives from it, and here is the one place every consumer
    passes through.
    """
    # "Newest" needs the fetch time in the sort: without it DuckDB's row order
    # decided which version survived, and it differs run to run - betas moved
    # and one model traded 115-141 fills on the same data (2026-10-09).
    # Empty closes (a partial fetch on 09-18) never win.
    df = _q(f"""
        select symbol, bar_time_utc, open, high, low, close, volume, fetched_at_utc
        from {_src('underlying_bars')}
        where interval = '1d' and symbol = '{symbol}' and close is not null
    """)
    df["bar_time"] = pd.to_datetime(df["bar_time_utc"], format="ISO8601", utc=True)
    if "fetched_at_utc" not in df:
        df["fetched_at_utc"] = ""
    return (df.sort_values(["bar_time", "fetched_at_utc"], kind="stable")
              .drop_duplicates(subset="bar_time", keep="last")
              .drop(columns="fetched_at_utc")
              .reset_index(drop=True))


def daily_bars_asof(symbol: str, as_of) -> pd.DataFrame:
    """Daily bars whose session had already closed at `as_of`.

    A daily bar for session D is stamped `D 04:00Z` - midnight Eastern, before
    the session it summarises - so a naive `bar_time < now` filter hands a
    14:00Z cycle the 16:00 ET close of its own day. Measured 2026-09-16: that
    moved `sigma_realised` by up to 1.1 vol points (PLTR 0.692 against 0.703)
    and every beta by up to 0.017.

    The cut is 16:00 **Eastern** on the bar's own date, done in the tz database
    so it follows DST without an offset written down anywhere. It is not the
    NYSE calendar: `sessions.nyse_sessions` spans one year by default and the
    bars go back five, so a calendar lookup silently deletes four years of
    history - which is what the first version of this function did. A half day
    closes at 13:00 ET and is therefore held back three hours longer than it
    needs to be; nothing in the replay asks for the current day's bar, so that
    costs nothing and errs the safe way.
    """
    bars = daily_bars(symbol)
    if bars.empty:
        return bars
    stamp = pd.Timestamp(as_of)
    stamp = stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")
    eastern = bars["bar_time"].dt.tz_convert(EASTERN)
    known_at = (eastern.dt.normalize() + pd.Timedelta(hours=16)).dt.tz_convert("UTC")
    return bars[known_at <= stamp].reset_index(drop=True)


@functools.lru_cache(maxsize=1)
def bars_5m() -> pd.DataFrame:
    """All stored 5-minute bars, sorted, for the spot-at-book-time lookup."""
    # One version per bar, newest fetch wins - as `daily_bars`, for its reason:
    # 20 FX/commodity bars were stored twice with different prices.
    df = _q(f"""
        select symbol, bar_time_utc, open, high, low, close, fetched_at_utc
        from {_src('underlying_bars')} where interval = '5m'
    """)
    df["bar_time"] = pd.to_datetime(df["bar_time_utc"], format="ISO8601", utc=True)
    if "fetched_at_utc" not in df:
        df["fetched_at_utc"] = ""
    return (df.sort_values(["symbol", "bar_time", "fetched_at_utc"], kind="stable")
              .drop_duplicates(subset=["symbol", "bar_time"], keep="last")
              .drop(columns="fetched_at_utc")
              .sort_values(["bar_time", "symbol"], kind="stable")
              .reset_index(drop=True))


def spot_at(times: pd.Series, symbols: pd.Series) -> pd.Series:
    """The last print known at each (symbol, time): the **open** of the
    5-minute bar containing it.

    MODELS 3.1 originally said the bar's *close*. A bar stamped 15:50Z covers
    15:50:00-15:54:59, so its close is a print from up to five minutes after a
    15:51:29 book - lookahead, and not a small one against barriers 0.3% away
    (P6 measured SPY's within-bar move at p90 0.15%, max 0.32% on 2026-09-16).
    The open of the containing bar is the first print at or after the bar's
    start, which is at or before the book time; it is what a trader looking at
    the tape at that moment could actually have seen. Review 2026-09-18.
    """
    left = (pd.DataFrame({"symbol": symbols.values,
                          "t": pd.to_datetime(pd.Series(times.values), utc=True)})
            .reset_index().sort_values("t"))
    right = (bars_5m()[["symbol", "bar_time", "open"]]
             .rename(columns={"bar_time": "t", "open": "spot"}).sort_values("t"))
    merged = pd.merge_asof(left, right, on="t", by="symbol", direction="backward",
                           tolerance=pd.Timedelta(minutes=30))
    return pd.Series(merged.sort_values("index")["spot"].values, index=times.index)


@functools.lru_cache(maxsize=64)
def session_bars_5m(symbol: str) -> pd.DataFrame:
    """One symbol's 5-minute bars inside NYSE regular hours, sorted.

    The rules text says only prices during regular trading hours count, and
    the store's 5-minute bars are not all regular hours: the commodity and FX
    ladders (DXY, WTI, XAGUSD, XAUUSD) carry 39,693 extended-hours bars between
    them (measured 2026-09-18); the equities carry none. Filtered in Eastern
    so it follows DST.
    """
    bars = bars_5m()
    mine = bars[bars["symbol"] == symbol]
    if mine.empty:
        return mine.reset_index(drop=True)
    eastern = mine["bar_time"].dt.tz_convert(EASTERN)
    minute = eastern.dt.hour * 60 + eastern.dt.minute
    return mine[(minute >= 9 * 60 + 30) & (minute < 16 * 60)].reset_index(drop=True)


@functools.lru_cache(maxsize=1)
def bars_1h() -> pd.DataFrame:
    """All stored 1-hour bars (P1's backfill, regular session only, from
    2023-10-19), sorted. Stamped at the bar's start like the 5-minute ones."""
    df = _q(f"""
        select distinct symbol, bar_time_utc, open, high, low, close
        from {_src('underlying_bars')} where interval = '1h'
    """)
    df["bar_time"] = pd.to_datetime(df["bar_time_utc"], format="ISO8601", utc=True)
    return df.sort_values("bar_time").reset_index(drop=True)


def _regular_hours(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars.reset_index(drop=True)
    eastern = bars["bar_time"].dt.tz_convert(EASTERN)
    minute = eastern.dt.hour * 60 + eastern.dt.minute
    return bars[(minute >= 9 * 60 + 30) & (minute < 16 * 60)].reset_index(drop=True)


@functools.lru_cache(maxsize=64)
def session_bars_1h(symbol: str) -> pd.DataFrame:
    bars = bars_1h()
    return _regular_hours(bars[bars["symbol"] == symbol])


def touched_between(symbol: str, barrier: float, is_up: bool, start, end) -> bool | None:
    """Did a finished regular-hours bar print the barrier in `[start, end)`?

    Three bar series are read and any print counts: the 5-minute bars (from
    2026-06-22), the 1-hour bars (from 2023-10-19) and the daily bars (five
    years), each admitted only once its period is over - `bar_time + 5 min`,
    `+ 1 h`, or 16:00 ET on the bar's date. The coarser series exist so the
    check is not blind before the 5-minute history starts; on any date all
    three cover, they agree by construction (a daily high is the max of its
    hourly highs is the max of its 5-minute highs).

    MODELS 3.1's "skip the market if S_book is already beyond B" compares one
    spot to the barrier, and a touch is not a level, it is a path: SPY printed
    749.60 at 19:25Z on 2026-09-16, bounced to 752.42, and at 19:36Z the
    engine priced the `hit_down 750` NO at 0.79 against an ask of 1c. The
    venue's rules say the market was already a YES. This is the path check.

    Only bars whose five minutes are up by `end` count, so the bar still
    forming cannot leak its high or low. Returns None when there is no bar to
    look at, which the caller must treat as "no evidence", not "no touch":
    the store's 5-minute bars begin 2026-06-22.
    """
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    pieces = []
    for bars, length in ((session_bars_5m(symbol), pd.Timedelta(minutes=5)),
                         (session_bars_1h(symbol), pd.Timedelta(hours=1))):
        if bars.empty:
            continue
        finished = bars["bar_time"] + length <= end
        pieces.append(bars[(bars["bar_time"] >= start) & finished][["high", "low"]])
    daily = daily_bars_asof(symbol, end)
    if not daily.empty:
        pieces.append(daily[daily["bar_time"] >= start][["high", "low"]])
    pieces = [p for p in pieces if not p.empty]
    if not pieces:
        return None
    window = pd.concat(pieces, ignore_index=True)
    if is_up:
        return bool((window["high"] >= barrier).any())
    return bool((window["low"] <= barrier).any())


@functools.lru_cache(maxsize=1)
def market_watch_terms() -> pd.DataFrame:
    """Per market: `created_at`, and whether its rules count touches only after
    creation (`from_creation`). See `sessions.watch_start` for why this exists.

    Latest row per market; the rules text is static per market.
    """
    df = _q(f"""
        select market_id, arg_max(created_at, fetched_at_utc) created_at,
               bool_or(rules_text ilike '%market creation%') from_creation
        from {_src('poly_markets')}
        where contract_type in ('hit_up', 'hit_down')
        group by market_id
    """)
    df["created_at"] = pd.to_datetime(df["created_at"], format="ISO8601", utc=True, errors="coerce")
    df["from_creation"] = df["from_creation"].fillna(False).astype(bool)
    return df.set_index("market_id")


def watch_start_for(market_id, deadline, period: str) -> pd.Timestamp:
    """`sessions.watch_start` for one market, with its terms looked up here."""
    from sessions import watch_start

    terms = market_watch_terms()
    key = str(market_id)
    if key not in terms.index:
        return watch_start(deadline, period)
    return watch_start(deadline, period, terms.at[key, "created_at"],
                       bool(terms.at[key, "from_creation"]))


# ---------------------------------------------------- rates, earnings, outcomes


def risk_free_rate(when: dt.datetime | str) -> float:
    """The 1-month T-bill on or before `when`, as a decimal. MODELS 3.1.

    It barely moves the fair value of a two-day barrier; it is kept because
    leaving it out is a decision nobody would remember making.
    """
    date = str(when)[:10]
    df = _q(f"""
        select obs_date, rate_percent from {_src('rates')}
        where series = 'DGS1MO' and rate_percent is not null and obs_date <= '{date}'
        order by obs_date desc limit 1
    """)
    return float(df["rate_percent"].iloc[0]) / 100.0 if len(df) else 0.0


@functools.lru_cache(maxsize=1)
def upcoming_earnings() -> pd.DataFrame:
    """Every upcoming earnings date ever fetched, with when it was first seen.

    The stamp is the bell: all 17 rows in the store (2026-09-18) carry
    `20:00Z`, i.e. 16:00 ET on the day, whether the company reports before the
    open or after the close. So a date compared against a session close must
    use half-open intervals - a report stamped at the deadline's own close is
    *after* the last candle the barrier could print on.
    """
    df = _q(f"""
        select symbol, earnings_date_utc, min(fetched_at_utc) first_seen_utc
        from {_src('earnings')}
        where kind = 'upcoming' and earnings_date_utc is not null
        group by 1, 2
    """)
    df["when"] = pd.to_datetime(df["earnings_date_utc"], format="ISO8601", utc=True,
                                errors="coerce")
    df["first_seen"] = pd.to_datetime(df["first_seen_utc"], format="ISO8601", utc=True,
                                      errors="coerce")
    return df.dropna(subset=["when"]).sort_values("when").reset_index(drop=True)


def earnings_between(symbol: str, start, end, as_of=None) -> bool:
    """Is there an earnings date in `[start, end)` for `symbol`?

    Half-open on purpose: see `upcoming_earnings` for the stamp convention.

    `as_of` (P6 finding 8): only dates the collector had seen by then count,
    so a replay cannot learn a date from a later fetch. The first fetch in the
    store is 2026-09-16T21:44Z, after every cycle of that day, so with a strict
    filter the flag would be uniformly False on the one day there is; the
    fallback is the *earliest* fetch. That is a documented approximation, not
    lookahead in any sense that matters: a confirmed earnings date is public
    weeks ahead, and every date in the store is at least two weeks past its
    first fetch.
    """
    df = upcoming_earnings()
    if as_of is not None and len(df):
        stamp = pd.Timestamp(as_of)
        stamp = stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")
        seen = df[df["first_seen"] <= stamp]
        df = seen if len(seen) else df[df["first_seen"] == df["first_seen"].min()]
    hit = df[(df["symbol"] == symbol) & (df["when"] >= start) & (df["when"] < end)]
    return bool(len(hit))


def resolutions_upto(when: dt.datetime | str) -> pd.DataFrame:
    """Latest resolution row per market that had settled by `when`.

    The harness replays cycles in order and must never see an outcome that had
    not happened yet; that is enforced here rather than in every caller.
    """
    # Compared as instants, not text: the column is ISO with a 'T' and
    # `str(pd.Timestamp)` has a space, so as strings every settlement on the
    # cutoff's own date sorted after it and the last day never settled
    # (review 2026-10-09).
    stamp = pd.Timestamp(when)
    stamp = (stamp.tz_localize("UTC") if stamp.tz is None else stamp.tz_convert("UTC")).isoformat()
    df = _q(f"""
        select market_id, result, settled_at_utc, ticker, contract_type, barrier,
               outcome_prices
        from (
            select *, row_number() over (partition by market_id
                                         order by fetched_at_utc desc) rn
            from {_src('resolutions')}
            where settled_at_utc is not null
              and cast(settled_at_utc as timestamptz) <= cast('{stamp}' as timestamptz)
        ) where rn = 1
    """)
    df["settled_at_utc"] = pd.to_datetime(df["settled_at_utc"], format="ISO8601",
                                          utc=True, errors="coerce")
    return df


# ------------------------------------------------- the engine's own output


FAIR_STORE = HERE / "store" / "fair"


def fair_values(date: str) -> pd.DataFrame:
    """Every fair value P2's engine stored for one date, one row per market-cycle.

    Read here rather than in `sim.py` for the reason the module docstring gives:
    `model/` has exactly one door to Parquet. The frame is returned whole and
    the replay slices it by `cycle_id`, because 21 cycles of one date is 14k
    rows and re-querying DuckDB per cycle costs more than holding it.
    """
    root = FAIR_STORE / f"date={date}"
    if not root.exists() or not any(root.glob("*.parquet")):
        return pd.DataFrame()
    df = _q(f"""
        select * from read_parquet('{root}/*.parquet', union_by_name=true)
    """)
    for col in ("deadline_utc", "book_timestamp_utc", "quote_time_utc", "computed_at_utc"):
        if col in df:
            df[col] = pd.to_datetime(df[col], format="ISO8601", utc=True, errors="coerce")
    return df


def fair_dates() -> list[str]:
    """Dates the engine has priced, in order."""
    if not FAIR_STORE.exists():
        return []
    return sorted(p.name.split("=", 1)[1] for p in FAIR_STORE.glob("date=*")
                  if any(p.glob("*.parquet")))


# ------------------------------------------------------- the collector's log


def runs(since: str | None = None, jobs: tuple[str, ...] = ("snapshot", "daily")) -> pd.DataFrame:
    """The collector's own record of every job it ran. MODELS 4.5's gap check.

    A simulation that silently replays a half-collected day reports a thin
    market rather than a sleeping laptop, which is what happened on 2026-09-17
    and is why this table is read at all. `counts_json` is parsed here because
    the two numbers worth watching live inside it: `tokens_requested` against
    `tokens_returned`, and `missing`.
    """
    import json

    where = f"and date >= '{since}'" if since else ""
    names = ", ".join(f"'{j}'" for j in jobs)
    df = _q(f"""
        select cycle_id, job, started_at_utc, ended_at_utc, duration_seconds,
               status, counts_json, failure_count, notes, date
        from {_src('runs')}
        where job in ({names}) {where}
        order by cycle_id
    """)
    if df.empty:
        return df
    for col in ("started_at_utc", "ended_at_utc"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", utc=True, errors="coerce")
    df["date"] = df["date"].astype(str)

    def field(blob, key):
        try:
            return float(json.loads(blob).get(key, float("nan")))
        except (TypeError, ValueError, AttributeError):
            return float("nan")

    for key in ("tokens_requested", "tokens_returned", "missing", "levels"):
        df[key] = [field(b, key) for b in df["counts_json"]]
    df["in_session"] = [in_session(c) for c in df["cycle_id"]]
    return df
