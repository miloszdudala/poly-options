"""A small, made-up store with the collector's own schemas.

    python model/run.py sample --synthetic --out model/samples/synthetic

One week (Mon 14 - Fri 18 Sep 2026), three cycles a session, NVDA and PLTR
(plus SPY, which the bar guard probes): 5-minute bars from a random walk,
a Black-Scholes option chain with a skew, hit and close-above markets with
books that are deliberately mispriced, and resolutions read off the bars.
Nothing in it is real. It exists so the whole pipeline can run, end to end,
in seconds: for tests, for checking a refactor against the old code, and for
trying a step before running it on the real store.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / "data" / "src"))
import schemas  # noqa: E402  (data/src/schemas.py: the collector's schemas)

DAYS = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]
CYCLE_HOURS = (14, 16, 18)                 # 10:00, 12:00, 14:00 ET
SPOTS = {"NVDA": 180.0, "PLTR": 170.0, "SPY": 660.0}
VOL = {"NVDA": 0.45, "PLTR": 0.55, "SPY": 0.15}
EXPIRIES = ["2026-09-18", "2026-09-25", "2026-10-02"]
WEEKLY_END = "2026-09-19T03:59:59Z"
MONTHLY_END = "2026-10-01T03:59:59Z"


def _write(root: Path, table: str, rows: list[dict], date: str) -> None:
    if not rows:
        return
    typed, schema = schemas.coerce(table, rows)
    out = root / table / f"date={date}"
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(typed, schema=schema), out / "part-0.parquet")


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs(spot, strike, years, sigma, rate, call):
    if years <= 0:
        return max(spot - strike, 0.0) if call else max(strike - spot, 0.0)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma ** 2) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    if call:
        return spot * _ncdf(d1) - strike * math.exp(-rate * years) * _ncdf(d2)
    return strike * math.exp(-rate * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def _bars(rng) -> dict[str, list[tuple[dt.datetime, float, float, float, float]]]:
    """5-minute regular-hours bars, 10 Sep to 18 Sep: (start, open, high, low, close)."""
    out = {}
    sessions = ["2026-09-10", "2026-09-11"] + DAYS
    for symbol, spot in SPOTS.items():
        step = VOL[symbol] / math.sqrt(252 * 78)
        price, bars = spot, []
        for day in sessions:
            start = dt.datetime.fromisoformat(day).replace(hour=13, minute=30)
            price *= math.exp(step * 2 * rng.standard_normal())          # overnight gap
            for i in range(78):
                path = price * np.exp(np.cumsum(step / 3 * rng.standard_normal(3)))
                o, c = price, float(path[-1])
                bars.append((start + dt.timedelta(minutes=5 * i), o,
                             max(o, c, float(path.max())), min(o, c, float(path.min())), c))
                price = c
        out[symbol] = bars
    return out


def _price_at(bars, when: dt.datetime) -> float:
    """The open of the bar containing `when` (the engine's spot)."""
    last = bars[0][1]
    for start, o, *_ in bars:
        if start > when:
            break
        last = o
    return last


def _touch(bars, barrier: float, up: bool, start: dt.datetime, end: dt.datetime):
    for t, _, high, low, _ in bars:
        if start <= t and t + dt.timedelta(minutes=5) <= end:
            if (up and high >= barrier) or (not up and low <= barrier):
                return t + dt.timedelta(minutes=5)
    return None


def build(root: Path, seed: int = 7) -> Path:
    """Write the synthetic store under `root` (a `parquet` directory layout)."""
    root = Path(root)
    rng = np.random.default_rng(seed)
    bars = _bars(rng)
    rate = 0.04

    # -- underlying bars, 5m and 1d, partitioned by bar date ------------------
    by_day: dict[str, list] = {}
    for symbol, rows in bars.items():
        days: dict[str, list] = {}
        for t, o, h, lo, c in rows:
            days.setdefault(t.date().isoformat(), []).append(
                {"source": "yfinance", "symbol": symbol, "interval": "5m",
                 "bar_time_utc": _iso(t), "open": o, "high": h, "low": lo, "close": c,
                 "cycle_id": "synthetic", "fetched_at_utc": _iso(t + dt.timedelta(minutes=10))})
        for day, rows_ in days.items():
            daily = {"source": "yfinance", "symbol": symbol, "interval": "1d",
                     "bar_time_utc": f"{day}T04:00:00Z", "open": rows_[0]["open"],
                     "high": max(r["high"] for r in rows_), "low": min(r["low"] for r in rows_),
                     "close": rows_[-1]["close"], "cycle_id": "synthetic",
                     "fetched_at_utc": f"{day}T21:00:00Z"}
            by_day.setdefault(day, []).extend(rows_ + [daily])
    for day, rows_ in by_day.items():
        _write(root, "underlying_bars", rows_, day)

    _write(root, "rates", [{"source": "fred", "series": "DGS1MO", "obs_date": "2026-09-10",
                            "rate_percent": 4.0, "fetched_at_utc": "2026-09-10T21:00:00Z"}],
           "2026-09-10")
    # An earnings date on the monthly deadline's own close: an earnings gap for
    # NVDA monthlies priced off the 2 Oct expiry.
    _write(root, "earnings", [{"source": "yfinance", "symbol": "NVDA", "kind": "upcoming",
                               "earnings_date_utc": "2026-09-30T20:00:00Z",
                               "fetched_at_utc": "2026-09-13T21:00:00Z"}], "2026-09-13")

    # -- markets ---------------------------------------------------------------
    markets = []
    created = dt.datetime(2026, 9, 14, 13, 0)
    for ticker in ("NVDA", "PLTR"):
        s0 = _price_at(bars[ticker], created)
        for period, end in (("weekly", WEEKLY_END), ("monthly", MONTHLY_END)):
            for k in (-0.06, -0.03, 0.03, 0.06):
                up = k > 0
                markets.append({"market_id": f"{ticker}-{period}-{'u' if up else 'd'}{abs(k):.2f}",
                                "ticker": ticker, "contract_type": "hit_up" if up else "hit_down",
                                "barrier": round(s0 * (1 + k), 1), "strike": None,
                                "period": period, "end_date": end})
        for k in (-0.02, 0.0, 0.02):
            markets.append({"market_id": f"{ticker}-close-{k:+.2f}", "ticker": ticker,
                            "contract_type": "close_above", "barrier": None,
                            "strike": float(round(s0 * (1 + k))), "period": "weekly",
                            "end_date": WEEKLY_END, "direction": "above"})

    # -- cycles: books, chain, quotes, runs ------------------------------------
    resolved: dict[str, dict] = {}
    for day in DAYS:
        books, chain, quotes, runs, market_rows = [], [], [], [], []
        for hour in CYCLE_HOURS:
            now = dt.datetime.fromisoformat(day).replace(hour=hour)
            cycle = now.strftime("%Y%m%dT%H%M%SZ") + "-synth0"
            quote_time = now - dt.timedelta(minutes=15)
            runs.append({"cycle_id": cycle, "fetched_at_utc": _iso(now), "job": "snapshot",
                         "started_at_utc": _iso(now), "ended_at_utc": _iso(now + dt.timedelta(minutes=2)),
                         "duration_seconds": 120.0, "status": "ok", "counts_json": "{}",
                         "failure_count": 0})
            for ticker in SPOTS:
                spot_q = _price_at(bars[ticker], quote_time)
                quotes.append({"cycle_id": cycle, "fetched_at_utc": _iso(now), "source": "cboe",
                               "symbol": ticker, "last_price": spot_q, "iv30": VOL[ticker]})
                if ticker == "SPY":
                    continue
                for expiry in EXPIRIES:
                    close = dt.datetime.fromisoformat(expiry).replace(hour=20)
                    years = (close - quote_time).total_seconds() / (365 * 86400)
                    for strike in np.arange(round(spot_q * 0.8), round(spot_q * 1.2) + 1, 1.0):
                        sigma = VOL[ticker] * (1 - 0.3 * math.log(strike / spot_q))
                        for call in (True, False):
                            mid = _bs(spot_q, strike, years, sigma, rate, call)
                            if mid < 0.02:
                                continue
                            chain.append({
                                "cycle_id": cycle, "fetched_at_utc": _iso(now), "source": "cboe",
                                "symbol": ticker, "underlying": ticker,
                                "contract_symbol": f"{ticker}{expiry}{'C' if call else 'P'}{strike:g}",
                                "option_type": "call" if call else "put", "expiry": expiry,
                                "strike": float(strike), "bid": mid * 0.99, "ask": mid * 1.01,
                                "last_price": mid, "last_trade_time_utc": _iso(quote_time),
                                "volume": 10.0, "open_interest": 500.0, "source_iv": sigma})
            for m in markets:
                ticker, up = m["ticker"], m["contract_type"] == "hit_up"
                spot = _price_at(bars[ticker], now)
                if m["contract_type"] == "close_above":
                    years = (dt.datetime(2026, 9, 18, 20) - now).total_seconds() / (365 * 86400)
                    d2 = (math.log(spot / m["strike"]) - 0.5 * VOL[ticker] ** 2 * years) \
                        / (VOL[ticker] * math.sqrt(years))
                    true = _ncdf(d2)
                else:
                    b = abs(math.log(m["barrier"] / spot))
                    end = dt.datetime(2026, 9, 18 if m["period"] == "weekly" else 30, 20)
                    years = max((end - now).total_seconds() / (365 * 86400), 1e-6)
                    true = 2 * _ncdf(-b / (VOL[ticker] * math.sqrt(years)))
                yes = float(np.clip(round(true + rng.normal(0, 0.08), 2), 0.01, 0.99))
                no = float(np.clip(round(1 - true + rng.normal(0, 0.08), 2), 0.01, 0.99))
                for index, ask in ((0, yes), (1, no)):
                    for rank in range(3):
                        level = {"cycle_id": cycle, "fetched_at_utc": _iso(now),
                                 "book_timestamp_utc": _iso(now - dt.timedelta(seconds=30)),
                                 "market_id": m["market_id"], "token_id": f"{m['market_id']}-{index}",
                                 "ticker": ticker, "contract_type": m["contract_type"],
                                 "strike": m["strike"], "barrier": m["barrier"],
                                 "end_date": m["end_date"], "period": m["period"],
                                 "outcome": "Yes" if index == 0 else "No", "outcome_index": index}
                        price = round(min(ask + 0.01 * rank, 0.99), 2)
                        books.append({**level, "side": "ask", "level_rank": rank,
                                      "depth_rank": rank, "price": price,
                                      "size": float(rng.integers(50, 400))})
                        if ask - 0.02 - 0.01 * rank > 0:
                            books.append({**level, "side": "bid", "level_rank": rank,
                                          "depth_rank": rank,
                                          "price": round(ask - 0.02 - 0.01 * rank, 2),
                                          "size": float(rng.integers(50, 400))})
                market_rows.append({**m, "cycle_id": cycle, "fetched_at_utc": _iso(now),
                                    "created_at": _iso(created), "outcome_0": "Yes",
                                    "outcome_1": "No", "rules_text": "regular trading hours",
                                    "active": True, "closed": False})
        _write(root, "poly_books", books, day)
        _write(root, "option_quotes", chain, day)
        _write(root, "underlying_quotes", quotes, day)
        _write(root, "runs", runs, day)
        _write(root, "poly_markets", market_rows, day)

    # -- resolutions, read off the bars ------------------------------------------
    for m in markets:
        ticker = m["ticker"]
        if m["contract_type"] == "close_above":
            last = bars[ticker][-1][4]
            resolved[m["market_id"]] = ("Yes" if last > m["strike"] else "No",
                                        dt.datetime(2026, 9, 18, 21))
            continue
        up = m["contract_type"] == "hit_up"
        touched = _touch(bars[ticker], m["barrier"], up, dt.datetime(2026, 9, 14, 13, 30),
                         dt.datetime(2026, 9, 18, 20))
        if touched is not None:
            resolved[m["market_id"]] = ("Yes", touched)
        elif m["period"] == "weekly":
            resolved[m["market_id"]] = ("No", dt.datetime(2026, 9, 18, 21))
    rows = [{"venue": "polymarket", "market_id": mid, "ticker": mid.split("-")[0],
             "result": result, "settled_at_utc": _iso(when), "outcome_prices": None,
             "cycle_id": "synthetic", "fetched_at_utc": _iso(when + dt.timedelta(minutes=5))}
            for mid, (result, when) in resolved.items()]
    _write(root, "resolutions", rows, "2026-09-18")
    return root


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "samples" / "synthetic"))
