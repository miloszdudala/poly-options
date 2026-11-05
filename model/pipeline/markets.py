"""Step 1 - markets: which markets of the cycle the pipeline looks at.

| variant | markets | decided / open |
|---|---|---|
| `polymarket_hit` | Polymarket "hit $B" of one `period`, minus `exclude_tickers` | the MVP universe |
| `polymarket_chain` | one `contract_type` (close_above, up_down, range) of one `period` | extension |
| `kalshi_index` | the Kalshi markets of the venue's `series`, on `underlying` | extension (`venue: kalshi`) |

Each variant also fixes the columns every later step relies on: `market_id`,
`ticker`, `contract_type`, `level`, `deadline` (the last NYSE close at or
before `end_date`: a barrier can only print in regular hours),
`yes_direction` and `yes_vol`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import store
from pipeline.core import Cycle, drop, empty_markets, sessions, variant
from sessions import deadline_close, sessions_remaining

HIT_TYPES = ("hit_up", "hit_down")


def contract_types(params: dict) -> tuple[str, ...]:
    """The book types a markets variant needs, from its config."""
    if params["use"] == "polymarket_hit":
        return HIT_TYPES
    return (params["contract_type"],) if "contract_type" in params else ()


def _deadlines(end_dates: pd.Series) -> pd.Series:
    closes = deadline_close(end_dates.values.astype("datetime64[ns]"), sessions())
    return pd.Series(pd.to_datetime(closes, utc=True), index=end_dates.index)


@variant("markets", "polymarket_hit")
def polymarket_hit(cycle: Cycle, p: dict) -> pd.DataFrame:
    frame = cycle.memo("hit_markets", lambda: store.market_book_times(cycle.id))
    if frame.empty:
        return empty_markets()
    frame = frame.copy()
    frame["market_id"] = frame["market_id"].astype(str)
    clean = frame.dropna(subset=["barrier", "end_date", "book_timestamp_utc", "fetched_at_utc"])
    clean = clean[clean["barrier"] > 0]
    # One clock per ticker over all of its hit markets, so a cached smile does
    # not depend on which markets asked for it first (`vol.py`); and the
    # cycle's longest horizon, which sizes the regular-hours draws (`prob.py`).
    cycle.memo("ticker_clock", lambda: clean.groupby("ticker")["fetched_at_utc"].max())
    cycle.memo("max_T_sessions", lambda: float(np.nanmax(sessions_remaining(
        clean["fetched_at_utc"].values, _deadlines(clean["end_date"]).values, sessions())))
        if len(clean) else 0.0)
    mine = frame[frame["period"] == p["period"]]
    if "include_tickers" in p:
        mine = mine[mine["ticker"].isin(p["include_tickers"])]
    mine = mine[~mine["ticker"].isin(p.get("exclude_tickers", []))].copy()
    mine["gate"] = None
    drop(mine, mine[["barrier", "end_date", "book_timestamp_utc", "fetched_at_utc"]]
         .isna().any(axis=1) | ~(mine["barrier"] > 0), "malformed market row")
    mine["level"] = mine["barrier"].astype(float)
    mine["deadline"] = _deadlines(mine["end_date"])
    mine["yes_direction"] = np.where(mine["contract_type"] == "hit_up", "up", "down")
    mine["yes_vol"] = "wild"
    return mine.reset_index(drop=True)


@variant("markets", "polymarket_chain")
def polymarket_chain(cycle: Cycle, p: dict) -> pd.DataFrame:
    books = cycle.books
    if books.empty:
        return empty_markets()
    frame = (books.groupby("market_id")
             .agg(ticker=("ticker", "first"), end_date=("end_date", "first"),
                  period=("period", "first"), fetched_at_utc=("fetched_at_utc", "max"),
                  contract_type=("contract_type", "first"))
             .reset_index())
    frame["market_id"] = frame["market_id"].astype(str)
    frame = frame[(frame["period"] == p["period"])
                  & ~frame["ticker"].isin(p.get("exclude_tickers", []))].copy()
    if frame.empty:
        return empty_markets()
    terms = cycle.memo(("terms", p["contract_type"]),
                       lambda: store.market_terms(p["contract_type"]))
    frame["gate"] = None
    for col in ("strike", "direction", "range_low", "range_high", "outcome_0", "outcome_1",
                "event_slug"):
        frame[col] = frame["market_id"].map(terms[col]) if len(terms) else None
    drop(frame, ~frame["market_id"].isin(terms.index), "no market terms")
    frame["deadline"] = _deadlines(frame["end_date"])
    kind = p["contract_type"]
    low, high = frame["range_low"].astype(float), frame["range_high"].astype(float)
    if kind == "close_above":
        frame["level"] = frame["strike"].astype(float)
        frame["yes_direction"] = np.where(frame["direction"] == "below", "down", "up")
    elif kind == "up_down":
        frame["level"] = np.nan                       # the day's open: `inputs.py`
        frame["yes_direction"] = np.where(frame["outcome_0"] == "Up", "up", "down")
    elif kind == "range":
        frame["level"] = np.where(low.notna(), low, high)
        frame["yes_direction"] = ["up" if pd.notna(lo) and pd.isna(hi) else
                                  "down" if pd.notna(hi) and pd.isna(lo) else None
                                  for lo, hi in zip(low, high)]
    else:
        raise ValueError(f"polymarket_chain does not know contract_type {kind!r}")
    frame["yes_vol"] = None
    return frame.reset_index(drop=True)


@variant("markets", "kalshi_index")
def kalshi_index(cycle: Cycle, p: dict) -> pd.DataFrame:
    """Kalshi index markets: one row per ticker in the cycle's books, priced off
    `underlying`'s chain at the market's `close_time`."""
    asks = cycle.asks
    if asks.empty:
        return empty_markets()
    series = ", ".join(f"'{s}'" for s in cycle.venue.series)
    terms = cycle.memo("kalshi_terms", lambda: store._q(f"""
        select ticker, strike_type, floor_strike, cap_strike, close_time
        from (select *, row_number() over (partition by ticker order by fetched_at_utc desc) rn
              from {store._src('kalshi_markets')} where series_ticker in ({series}))
        where rn = 1
    """).set_index("ticker"))
    frame = pd.DataFrame({"market_id": sorted(asks["market_id"].unique())})
    frame["ticker"] = p["underlying"]
    frame["period"] = p["period"]
    frame["fetched_at_utc"] = pd.Timestamp(asks["fetched_at_utc"].max())
    frame["gate"] = None
    for col in ("strike_type", "floor_strike", "cap_strike", "close_time"):
        frame[col] = frame["market_id"].map(terms[col]) if len(terms) else None
    drop(frame, ~frame["market_id"].isin(terms.index), "no market terms")
    frame["deadline"] = pd.to_datetime(frame["close_time"], format="ISO8601", utc=True,
                                       errors="coerce")
    frame["contract_type"] = frame["strike_type"].astype(str)
    frame["level"] = frame["floor_strike"].where(frame["floor_strike"].notna(), frame["cap_strike"])
    kind = frame["strike_type"].fillna("")
    frame["yes_direction"] = ["up" if k.startswith("greater") else
                              "down" if k.startswith("less") else None for k in kind]
    frame["yes_vol"] = None
    return frame
