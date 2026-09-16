"""Option chains from Cboe delayed quotes and yfinance.

Both sources are collected when available so they can be cross-checked. The
source's own IV/greeks are stored verbatim but treated as unverified; we keep
every raw input needed to recompute IV ourselves later.
"""
from __future__ import annotations

import datetime as dt
import re

from config import Underlying, markets_cfg, underlyings_cfg
from levels import StrikeFilter
from storage import raw_enabled, write_raw, write_table
from util import Http, get_logger, market_tz, now_utc, parse_iso, utc_iso, yf_pause

log = get_logger("options")

# OCC-style contract symbol: ROOT + YYMMDD + C/P + strike * 1000, zero padded.
OCC_RE = re.compile(r"^([A-Z0-9^.]{1,6}?)(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str) -> dict | None:
    """Split an OCC contract symbol into root, expiry, type and strike."""
    m = OCC_RE.match((symbol or "").strip().upper())
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    try:
        expiry = dt.datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        return None
    return {
        "root": root,
        "expiry": expiry.isoformat(),
        "option_type": "call" if cp == "C" else "put",
        "strike": int(strike) / 1000.0,
    }


def select_expiries(expiries: list[str], today: dt.date | None = None) -> list[str]:
    """All expiries within max_days_out, plus the December expiry nearest year-end."""
    cfg = underlyings_cfg()["options"]
    today = today or now_utc().astimezone(market_tz()).date()
    horizon = today + dt.timedelta(days=cfg["max_days_out"])

    parsed: list[dt.date] = []
    for text in expiries:
        try:
            parsed.append(dt.date.fromisoformat(str(text)[:10]))
        except ValueError:
            continue
    parsed = sorted(set(parsed))

    keep = {d for d in parsed if today <= d <= horizon}

    if cfg.get("include_year_end_december"):
        # Year-end Kalshi markets settle on the last session of the year, so take
        # the December expiry closest to 31 December (this year's, else the next).
        decembers = [d for d in parsed if d.month == 12 and d >= today]
        if decembers:
            year = min(d.year for d in decembers)
            same_year = [d for d in decembers if d.year == year]
            keep.add(min(same_year, key=lambda d: abs((d - dt.date(year, 12, 31)).days)))

    return [d.isoformat() for d in sorted(keep)]


# --------------------------------------------------------------------- Cboe


def fetch_cboe(http: Http, und: Underlying, cycle_id: str,
               strikes: "StrikeFilter | None" = None) -> tuple[list[dict], list[dict]]:
    """Cboe delayed quotes. Returns (option rows, underlying quote rows).

    Indices take a leading underscore (_SPX); the plain symbol returns 403.
    """
    cfg = markets_cfg()["cboe"]
    url = f"{cfg['base']}/{und.cboe_symbol}.json"
    payload = http.get(url, venue="cboe", identifier=und.symbol)
    if not payload:
        return [], []

    if raw_enabled("option_chains"):
        write_raw("cboe", und.symbol, payload, cycle_id)
    fetched_at = utc_iso()
    data = payload.get("data") or {}
    # Cboe publishes a naive timestamp in US Eastern.
    src_ts = parse_iso(payload.get("timestamp"), assume_tz=market_tz())
    src_iso = src_ts.isoformat() if src_ts else None

    wanted = set(select_expiries([
        (parse_occ(o.get("option", "")) or {}).get("expiry")
        for o in data.get("options") or []
        if parse_occ(o.get("option", ""))
    ]))

    spot_price = data.get("current_price")
    rows = []
    for o in data.get("options") or []:
        occ = parse_occ(o.get("option", ""))
        if not occ or occ["expiry"] not in wanted:
            continue
        if strikes and not strikes.accepts(und.symbol, occ["strike"], spot_price):
            continue
        last_trade = parse_iso(o.get("last_trade_time"), assume_tz=market_tz())
        rows.append({
            "cycle_id": cycle_id,
            "fetched_at_utc": fetched_at,
            "source": "cboe",
            "symbol": und.symbol,
            "underlying": und.symbol,
            "contract_symbol": o.get("option"),
            "option_type": occ["option_type"],
            "expiry": occ["expiry"],
            "strike": occ["strike"],
            "bid": o.get("bid"),
            "ask": o.get("ask"),
            "bid_size": o.get("bid_size"),
            "ask_size": o.get("ask_size"),
            "last_price": o.get("last_trade_price"),
            "last_trade_time_utc": last_trade.isoformat() if last_trade else None,
            "volume": o.get("volume"),
            "open_interest": o.get("open_interest"),
            "source_iv": o.get("iv"),
            "source_delta": o.get("delta"),
            "source_gamma": o.get("gamma"),
            "source_theta": o.get("theta"),
            "source_vega": o.get("vega"),
            "source_rho": o.get("rho"),
            "source_theo": o.get("theo"),
            "source_quote_time_utc": src_iso,
            "open": o.get("open"),
            "high": o.get("high"),
            "low": o.get("low"),
            "prev_day_close": o.get("prev_day_close"),
            "in_the_money": None,
        })

    spot = [{
        "cycle_id": cycle_id,
        "fetched_at_utc": fetched_at,
        "source": "cboe",
        "symbol": und.symbol,
        "last_price": data.get("current_price"),
        "bid": data.get("bid"),
        "ask": data.get("ask"),
        "bid_size": data.get("bid_size"),
        "ask_size": data.get("ask_size"),
        "open": data.get("open"),
        "high": data.get("high"),
        "low": data.get("low"),
        "prev_close": data.get("prev_day_close"),
        "volume": data.get("volume"),
        "iv30": data.get("iv30"),
        "source_quote_time_utc": src_iso,
    }] if data else []

    return rows, spot


# ----------------------------------------------------------------- yfinance


def fetch_yfinance(und: Underlying, cycle_id: str, failures: list,
                   strikes: "StrikeFilter | None" = None,
                   spot: float | None = None) -> tuple[list[dict], list[dict]]:
    """yfinance chain + spot. Never raises: one bad ticker must not stop the run."""
    import yfinance as yf

    fetched_at = utc_iso()
    rows: list[dict] = []
    spot: list[dict] = []
    try:
        ticker = yf.Ticker(und.yf_symbol)
        expiries = select_expiries(list(ticker.options or []))
    except Exception as exc:
        failures.append({"venue": "yfinance", "identifier": und.symbol,
                         "url": f"yf:{und.yf_symbol}", "error": f"expiries: {exc}",
                         "failed_at_utc": utc_iso()})
        return [], []

    if not expiries:
        log.info("%s: no yfinance expiries in horizon (no chain?)", und.symbol)

    for expiry in expiries:
        yf_pause()
        try:
            chain = ticker.option_chain(expiry)
        except Exception as exc:
            failures.append({"venue": "yfinance", "identifier": f"{und.symbol}:{expiry}",
                             "url": f"yf:{und.yf_symbol}", "error": str(exc),
                             "failed_at_utc": utc_iso()})
            continue
        for kind, frame in (("call", chain.calls), ("put", chain.puts)):
            for rec in frame.to_dict("records"):
                if strikes and not strikes.accepts(und.symbol, rec.get("strike"), spot):
                    continue
                last_trade = rec.get("lastTradeDate")
                rows.append({
                    "cycle_id": cycle_id,
                    "fetched_at_utc": fetched_at,
                    "source": "yfinance",
                    "symbol": und.symbol,
                    "underlying": und.symbol,
                    "contract_symbol": rec.get("contractSymbol"),
                    "option_type": kind,
                    "expiry": expiry,
                    "strike": rec.get("strike"),
                    "bid": rec.get("bid"),
                    "ask": rec.get("ask"),
                    "bid_size": None,   # yfinance does not expose quote sizes
                    "ask_size": None,
                    "last_price": rec.get("lastPrice"),
                    # yfinance's own per-contract quote timestamp.
                    "last_trade_time_utc": last_trade.isoformat() if last_trade is not None and str(last_trade) != "NaT" else None,
                    "volume": rec.get("volume"),
                    "open_interest": rec.get("openInterest"),
                    "source_iv": rec.get("impliedVolatility"),
                    "source_delta": None, "source_gamma": None, "source_theta": None,
                    "source_vega": None, "source_rho": None, "source_theo": None,
                    "source_quote_time_utc": None,
                    "open": None, "high": None, "low": None,
                    "prev_day_close": None,
                    "in_the_money": rec.get("inTheMoney"),
                })

    yf_pause()
    try:
        fast = ticker.fast_info
        spot.append({
            "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
            "source": "yfinance", "symbol": und.symbol,
            "last_price": fast.get("lastPrice") if hasattr(fast, "get") else fast.last_price,
            "bid": None, "ask": None, "bid_size": None, "ask_size": None,
            "open": getattr(fast, "open", None), "high": getattr(fast, "day_high", None),
            "low": getattr(fast, "day_low", None),
            "prev_close": getattr(fast, "previous_close", None),
            "volume": getattr(fast, "last_volume", None), "iv30": None,
            "source_quote_time_utc": None,
        })
    except Exception as exc:
        failures.append({"venue": "yfinance", "identifier": f"{und.symbol}:spot",
                         "url": f"yf:{und.yf_symbol}", "error": str(exc),
                         "failed_at_utc": utc_iso()})

    return rows, spot


def collect_options(http: Http, underlyings: list[Underlying], cycle_id: str,
                    include_yfinance: bool | None = None) -> dict:
    """Collect option chains; failures are isolated per underlying.

    Cboe is the per-cycle source: it covers all 25 tickers in ~29 s and carries
    bid/ask sizes, greeks and per-contract quote times. yfinance costs ~406 s for
    the same tickers because every expiry is a separate paced request, so by
    default it runs only in the daily job, as an independent cross-check.
    """
    use_cboe = markets_cfg()["cboe"].get("enabled", True)
    if include_yfinance is None:
        include_yfinance = underlyings_cfg()["options"].get("yfinance_every_cycle", False)

    strikes = StrikeFilter()
    opt_rows: list[dict] = []
    spot_rows: list[dict] = []
    per_source = {"cboe": 0, "yfinance": 0}

    for und in underlyings:
        spot = None
        if use_cboe:
            try:
                rows, spot_got = fetch_cboe(http, und, cycle_id, strikes)
            except Exception as exc:
                log.exception("cboe failed for %s", und.symbol)
                http.failures.append({"venue": "cboe", "identifier": und.symbol,
                                      "url": "", "error": str(exc),
                                      "failed_at_utc": utc_iso()})
                rows, spot_got = [], []
            opt_rows += rows
            spot_rows += spot_got
            per_source["cboe"] += len(rows)
            if spot_got:
                spot = spot_got[0].get("last_price")

        if include_yfinance:
            try:
                rows, spot_got = fetch_yfinance(und, cycle_id, http.failures, strikes, spot)
            except Exception:
                log.exception("yfinance failed for %s", und.symbol)
                rows, spot_got = [], []
            opt_rows += rows
            spot_rows += spot_got
            per_source["yfinance"] += len(rows)

    log.info("options: cboe=%d yfinance=%d | %s",
             per_source["cboe"], per_source["yfinance"], strikes.summary())

    if opt_rows:
        write_table("option_quotes", opt_rows)
    if spot_rows:
        write_table("underlying_quotes", spot_rows)
    return {"option_quotes": len(opt_rows), "underlying_quotes": len(spot_rows),
            "strikes_dropped": strikes.dropped, **per_source}
