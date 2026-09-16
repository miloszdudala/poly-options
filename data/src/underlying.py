"""Underlying price paths: daily OHLCV backfill and intraday bars.

Hit markets can resolve on intraday extremes, so intraday bars are pulled every
day and appended, because yfinance only retains a few days of 1-minute history.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from config import PARQUET_DIR, Underlying, underlyings_cfg
from storage import write_table
from util import get_logger, market_tz, utc_iso, yf_pause

log = get_logger("underlying")


def _bar_rows(frame: "pd.DataFrame", symbol: str, interval: str,
              cycle_id: str, fetched_at: str) -> list[dict]:
    rows = []
    for idx, rec in frame.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        rows.append({
            "cycle_id": cycle_id,
            "fetched_at_utc": fetched_at,
            "source": "yfinance",
            "symbol": symbol,
            "interval": interval,
            "bar_time_utc": ts.astimezone(dt.timezone.utc).isoformat(),
            "bar_time_market": ts.astimezone(market_tz()).isoformat(),
            "open": rec.get("Open"), "high": rec.get("High"),
            "low": rec.get("Low"), "close": rec.get("Close"),
            "volume": rec.get("Volume"),
            "dividends": rec.get("Dividends"),
            "stock_splits": rec.get("Stock Splits"),
        })
    return rows


def _last_bar_time() -> dict[tuple[str, str], str]:
    """Newest stored bar per (symbol, interval), so we only fetch what is new."""
    root = PARQUET_DIR / "underlying_bars"
    if not root.exists() or not any(root.rglob("*.parquet")):
        return {}
    import duckdb

    try:
        rows = duckdb.sql(
            f"""select symbol, interval, max(bar_time_utc) mx
                from read_parquet('{root}/**/*.parquet', hive_partitioning=true)
                group by 1, 2"""
        ).fetchall()
    except Exception as exc:  # a partial/corrupt partition must not stop the run
        log.warning("could not read existing bars, refetching in full: %s", exc)
        return {}
    return {(r[0], r[1]): r[2] for r in rows if r[2]}


def collect_bars(symbols: list[tuple[str, str]], cycle_id: str, failures: list,
                 daily_period: str | None = None) -> dict:
    """Daily OHLCV plus intraday bars, fetched incrementally.

    The first run backfills; later runs ask only for sessions newer than what is
    already stored. Re-downloading five years every day wrote ~1.4 GB/yr of rows
    that were already on disk.

    `symbols` is a list of (canonical_symbol, yfinance_symbol).
    """
    import datetime as _dt

    import yfinance as yf

    cfg = underlyings_cfg().get("bars", {}) or {}
    daily_period = daily_period or cfg.get("daily_backfill_period", "5y")
    intraday = cfg.get("intraday") or [{"interval": "5m", "period": "60d"}]

    fetched_at = utc_iso()
    counts: dict = {"daily": 0}
    rows: list[dict] = []
    seen = _last_bar_time()

    def fetch(ticker, symbol, yf_symbol, interval, period):
        """Ask only for what is missing, falling back to the full window."""
        last = seen.get((symbol, interval))
        kwargs = {"interval": interval, "auto_adjust": False}
        if last:
            start = _dt.datetime.fromisoformat(last) + _dt.timedelta(seconds=1)
            kwargs["start"] = start
        else:
            kwargs["period"] = period
        return ticker.history(**kwargs)

    for symbol, yf_symbol in symbols:
        ticker = yf.Ticker(yf_symbol)

        for interval, period in [("1d", daily_period)] + [
                (i["interval"], i["period"]) for i in intraday]:
            yf_pause()
            try:
                bars = fetch(ticker, symbol, yf_symbol, interval, period)
            except Exception as exc:
                failures.append({"venue": "yfinance", "identifier": f"{symbol}:{interval}",
                                 "url": f"yf:{yf_symbol}", "error": str(exc),
                                 "failed_at_utc": utc_iso()})
                continue
            if bars is None or len(bars) == 0:
                continue
            got = _bar_rows(bars, symbol, interval, cycle_id, fetched_at)
            rows += got
            key = "daily" if interval == "1d" else interval
            counts[key] = counts.get(key, 0) + len(got)

        log.info("%-8s bars %s", symbol,
                 " ".join(f"{k}={v}" for k, v in counts.items() if k != "total_rows"))

    if rows:
        write_table("underlying_bars", rows)
    counts["total_rows"] = len(rows)
    counts["incremental"] = bool(seen)
    return counts


def collect_dividends_and_earnings(symbols: list[tuple[str, str]], cycle_id: str,
                                   failures: list) -> dict:
    """Dividend history, upcoming ex-dates, and past/upcoming earnings dates."""
    import yfinance as yf

    fetched_at = utc_iso()
    div_rows: list[dict] = []
    earn_rows: list[dict] = []

    for symbol, yf_symbol in symbols:
        ticker = yf.Ticker(yf_symbol)

        yf_pause()
        try:
            divs = ticker.dividends
            for idx, amount in (divs.items() if divs is not None else []):
                ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                div_rows.append({
                    "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                    "source": "yfinance", "symbol": symbol, "kind": "historical",
                    "ex_date": ts.date().isoformat(), "pay_date": None,
                    "amount": float(amount), "dividend_yield": None,
                })
        except Exception as exc:
            failures.append({"venue": "yfinance", "identifier": f"{symbol}:dividends",
                             "url": f"yf:{yf_symbol}", "error": str(exc),
                             "failed_at_utc": utc_iso()})

        # Announced upcoming ex-dividend date, if the source exposes one.
        yf_pause()
        try:
            info = ticker.info or {}
            ex_ts = info.get("exDividendDate")
            if ex_ts:
                ex_date = dt.datetime.fromtimestamp(float(ex_ts), tz=dt.timezone.utc).date()
                div_rows.append({
                    "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                    "source": "yfinance", "symbol": symbol, "kind": "upcoming",
                    "ex_date": ex_date.isoformat(),
                    "pay_date": None,
                    "amount": info.get("dividendRate"),
                    "dividend_yield": info.get("dividendYield"),
                })
        except Exception as exc:
            failures.append({"venue": "yfinance", "identifier": f"{symbol}:info",
                             "url": f"yf:{yf_symbol}", "error": str(exc),
                             "failed_at_utc": utc_iso()})

        # --- earnings dates: jumps matter for interpolating across expiries ---
        if symbol in set(underlyings_cfg().get("no_earnings") or []):
            continue  # index or ETF: no earnings to fetch
        yf_pause()
        try:
            dates = ticker.get_earnings_dates(limit=24)
            now = dt.datetime.now(dt.timezone.utc)
            for idx, rec in (dates.iterrows() if dates is not None else []):
                ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
                ts = ts.astimezone(dt.timezone.utc)
                earn_rows.append({
                    "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                    "source": "yfinance", "symbol": symbol,
                    "kind": "upcoming" if ts > now else "past",
                    "earnings_date_utc": ts.isoformat(),
                    "eps_estimate": rec.get("EPS Estimate"),
                    "eps_actual": rec.get("Reported EPS"),
                    "surprise_percent": rec.get("Surprise(%)"),
                })
        except Exception as exc:
            failures.append({"venue": "yfinance", "identifier": f"{symbol}:earnings",
                             "url": f"yf:{yf_symbol}", "error": str(exc),
                             "failed_at_utc": utc_iso()})

    if div_rows:
        write_table("dividends", div_rows)
    if earn_rows:
        write_table("earnings", earn_rows)
    return {"dividends": len(div_rows), "earnings": len(earn_rows)}


def futures_symbols() -> list[tuple[str, str]]:
    """Free front-month futures proxies for the commodity / FX hit markets."""
    cfg = underlyings_cfg().get("futures", {}) or {}
    return [(name, spec["yf_symbol"]) for name, spec in cfg.items()]
