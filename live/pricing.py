"""Fair values for live trading, from `live/pricing_engine/` (a frozen copy of
the model/ engine) and the collector's snapshots in `data/store`.

Nothing here imports `model/`. The only things shared with the replays are the
collector (`data/`) and its store, which both sides read and neither owns.

**Two clocks.** The collector writes a snapshot (books plus option chains)
every 15 minutes; the live loop decides every minute. Each new snapshot is
priced once by the engine (~10 s). In between, the loop moves each market's
fair value along its delta to the current spot,
`fair + delta x (spot_now - spot_at_snapshot)`, and walks the live book, not
the snapshot's. A ticker that has moved more than `max_spot_move` since the
snapshot is skipped until the next one, because a first-order delta is not
good for large moves near a barrier.

**Same-day bars.** The engine needs 5-minute bars for the spot at book time
and for the touch check (a barrier that printed earlier today is already a
YES; the 2026-10-09 review found 40 replay fills on such barriers). The
collector fetches bars only after the close, so this module downloads today's
and hands them to the engine **in memory**. It never writes them to
`data/store`: the bar still forming would be stored with an unfinished high
and low, the daily job fetches only bars newer than the newest stored, and the
replays' touch data would be wrong for good. Using the forming bar here is
safe: `touched_between` counts only bars whose five minutes are over, and
`spot_at` takes a bar's open, which is known as soon as the bar starts.
"""
from __future__ import annotations

import datetime as dt
import functools
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(HERE / "pricing_engine") not in sys.path:
    sys.path.insert(1, str(HERE / "pricing_engine"))

import data  # noqa: E402  (live/pricing_engine/data.py)
from config import underlyings, underlyings_cfg  # noqa: E402  (data/src/config.py)
from engine import price_cycle  # noqa: E402
from sessions import nyse_sessions  # noqa: E402

_STORE_BARS_5M = data.bars_5m
FLAGS = ("touch_pending", "deep_otm", "no_chain")


def cycle_time(cycle_id: str) -> dt.datetime:
    return dt.datetime.strptime(cycle_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def latest_cycle(now: dt.datetime) -> str | None:
    """The newest cleanly finished session cycle of `now`'s UTC date."""
    cycles = data.session_cycles(now.strftime("%Y-%m-%d"))
    return cycles[-1] if cycles else None


def session_bounds(now: dt.datetime) -> tuple[dt.datetime, dt.datetime] | None:
    """Open and close (UTC) of the NYSE session containing `now`, if any.
    Half days and holidays come from the exchange calendar."""
    sess = nyse_sessions()
    # nyse_sessions holds naive UTC (it stores `.values`).
    ts = pd.Timestamp(now).tz_convert("UTC").tz_localize(None)
    hit = sess[(sess["open"] <= ts) & (ts < sess["close"])]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return (row["open"].tz_localize("UTC").to_pydatetime(),
            row["close"].tz_localize("UTC").to_pydatetime())


def in_session(now: dt.datetime) -> bool:
    return session_bounds(now) is not None


# ------------------------------------------------------------------- bars


def _yf_symbols() -> dict[str, str]:
    """canonical -> yfinance spelling, as the collector's daily job uses."""
    out = {u.symbol: u.yf_symbol for u in underlyings()}
    for name, spec in (underlyings_cfg().get("futures") or {}).items():
        out[name] = spec["yf_symbol"]
    return out


def fetch_today_bars(tickers) -> pd.DataFrame:
    """Today's 5-minute bars for `tickers`, the forming one included, in
    `data.bars_5m`'s columns. A ticker yfinance cannot serve is left out; the
    engine then falls back to the delayed Cboe spot and flags it."""
    import yfinance as yf

    spell = _yf_symbols()
    frames = []
    for ticker in sorted(set(tickers)):
        try:
            hist = yf.Ticker(spell.get(ticker, ticker)).history(
                period="1d", interval="5m", prepost=False, auto_adjust=False)
        except Exception:   # one bad symbol must not cost the others
            continue
        if hist.empty:
            continue
        frame = hist.reset_index().rename(columns={"Datetime": "bar_time", "Open": "open",
                                                   "High": "high", "Low": "low",
                                                   "Close": "close"})
        frame["bar_time"] = pd.to_datetime(frame["bar_time"], utc=True)
        frame["symbol"] = ticker
        frames.append(frame[["symbol", "bar_time", "open", "high", "low", "close"]])
    if not frames:
        return pd.DataFrame(columns=["symbol", "bar_time", "open", "high", "low", "close"])
    out = pd.concat(frames, ignore_index=True)
    out["bar_time_utc"] = out["bar_time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def _fresh_engine_state(extra: pd.DataFrame) -> None:
    """Drop every cached read, then point the engine at stored bars plus
    `extra`, newest version winning.

    A loop runs all session, and the engine's caches were written for one-shot
    runs: `market_watch_terms` cached at the open would not know the barriers
    Polymarket lists at 12:40 ET, and their touch window would be wrong.
    """
    for name in dir(data):
        fn = getattr(data, name)
        if hasattr(fn, "cache_clear") and name != "_con":
            fn.cache_clear()
    data.bars_5m = _STORE_BARS_5M
    if extra.empty:
        return
    stored = _STORE_BARS_5M()
    merged = (pd.concat([stored, extra[stored.columns.intersection(extra.columns)]],
                        ignore_index=True)
              .drop_duplicates(subset=["symbol", "bar_time"], keep="last")
              .sort_values(["bar_time", "symbol"], kind="stable")
              .reset_index(drop=True))
    data.bars_5m = functools.lru_cache(maxsize=1)(lambda: merged)


def tradeable_tickers(fair: pd.DataFrame) -> list[str]:
    if fair.empty:
        return []
    ok = np.ones(len(fair), dtype=bool)
    for flag in FLAGS:
        if flag in fair:
            ok &= ~fair[flag].fillna(False).astype(bool).to_numpy()
    return sorted(fair.loc[ok, "ticker"].dropna().unique())


# ---------------------------------------------------------------- pricing


def price(cycle_id: str, fresh_bars: bool = True) -> dict:
    """Fair values and stored books for one snapshot cycle.

    `fresh_bars=False` prices on stored bars only, which is right for a past
    cycle (`trader.py run --cycle`) and wrong for today's.
    """
    books = data.books(cycle_id)
    bars = pd.DataFrame()
    if fresh_bars and not books.empty:
        bars = fetch_today_bars(books["ticker"].dropna().unique().tolist())
    _fresh_engine_state(bars)
    fair, stat = price_cycle(cycle_id)
    return {"cycle_id": cycle_id, "fair": fair, "books": books, "bars": bars, "stat": stat}


def spot_now(bars: pd.DataFrame) -> pd.Series:
    """Latest print per ticker: the close of its newest (possibly forming) bar."""
    if bars.empty:
        return pd.Series(dtype=float)
    return bars.sort_values("bar_time").groupby("symbol")["close"].last()


def move_fair(fair: pd.DataFrame, bars: pd.DataFrame, max_move: float) -> pd.DataFrame:
    """The snapshot's fair values moved along their delta to the current spot.

    `delta` is dP(YES)/dS per unit of spot (fair.touch_delta). Rows whose
    ticker has no current spot, or moved more than `max_move` (a fraction)
    since the snapshot, are dropped: no number is better than a bad one.
    """
    if fair.empty:
        return fair
    now = spot_now(bars)
    out = fair.copy()
    out["spot_now"] = out["ticker"].map(now)
    move = (out["spot_now"] - out["spot"]) / out["spot"]
    keep = out["spot_now"].notna() & out["spot"].notna() & (move.abs() <= max_move)
    out = out[keep].copy()
    shift = out["delta"].fillna(0.0) * (out["spot_now"] - out["spot"])
    for col in ("fair_K", "fair_atm"):
        out[col] = (out[col] + shift).clip(0.0, 1.0)
    out["spot"] = out["spot_now"]
    return out.drop(columns="spot_now")


def printed_since(bars: pd.DataFrame, ticker: str, barrier: float, is_up: bool,
                  since: dt.datetime) -> bool:
    """Has any bar from `since` on, **including the one still forming**, reached
    the barrier? The guard for the minutes between the snapshot and the order,
    which the engine's finished-bar check cannot see. Conservative on purpose:
    a forming bar that touched is a market already lost."""
    if bars.empty:
        return False
    mine = bars[(bars["symbol"] == ticker) & (bars["bar_time"] + pd.Timedelta(minutes=5)
                                               > pd.Timestamp(since))]
    if mine.empty:
        return False
    return bool(mine["high"].max() >= barrier) if is_up else bool(mine["low"].min() <= barrier)


# -------------------------------------------------------------- collector


def start_snapshot(log_path: Path) -> subprocess.Popen:
    """Run the collector's snapshot job in the background, exactly as launchd
    would. For when the scheduled jobs are not installed or the Mac slept."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a")
    return subprocess.Popen([sys.executable, str(REPO / "data" / "src" / "jobs.py"), "snapshot"],
                            cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
