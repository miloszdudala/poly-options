"""The price levels we actually track, per underlying.

Used to decide which option strikes are worth storing. Cboe publishes every
strike a ticker has (SPX runs 200 to 13,000 against a 7,585 spot), and almost
all of them are useless for pricing a Polymarket bet at $400. A strike earns its
place if it is either near spot, or near a level some tracked market settles on.

Keyed off the levels rather than a fixed percentage band because the tracked
levels are not clustered around spot: NFLX markets run -74% to +80% of spot and
OPEN's to +279%, so a fixed band would silently drop the very strikes needed.
"""
from __future__ import annotations

import collections

from config import markets_cfg
from storage import read_meta
from util import get_logger

log = get_logger("levels")


def tracked_levels() -> dict[str, set[float]]:
    """underlying symbol -> the set of price levels tracked markets settle on."""
    out: dict[str, set[float]] = collections.defaultdict(set)

    for market in (read_meta("poly_markets_latest") or {}).get("markets", []):
        ticker = market.get("ticker")
        if not ticker:
            continue
        for key in ("strike", "barrier", "range_low", "range_high"):
            value = market.get(key)
            if value is not None:
                try:
                    out[ticker].add(float(value))
                except (TypeError, ValueError):
                    continue

    series_map = markets_cfg()["kalshi"].get("series_underlying", {}) or {}
    for market in (read_meta("kalshi_markets_latest") or {}).get("markets", []):
        symbol = series_map.get(market.get("series_ticker"))
        if not symbol:
            continue
        for key in ("floor_strike", "cap_strike"):
            value = market.get(key)
            if value is not None:
                try:
                    out[symbol].add(float(value))
                except (TypeError, ValueError):
                    continue

    return dict(out)


def keep_strike(strike: float, spot: float | None, levels: set[float],
                spot_band: float, level_margin: float) -> bool:
    """True if this strike is close to spot, or to a level we track."""
    if strike is None:
        return False
    if spot and spot > 0 and abs(strike / spot - 1.0) <= spot_band:
        return True
    for level in levels:
        if level > 0 and abs(strike / level - 1.0) <= level_margin:
            return True
    return False


class StrikeFilter:
    """Decides which strikes to store, for one collection run."""

    def __init__(self):
        cfg = __import__("config").underlyings_cfg()["options"].get("strikes", {}) or {}
        self.enabled = cfg.get("enabled", True)
        self.spot_band = float(cfg.get("spot_band", 0.30))
        self.level_margin = float(cfg.get("level_margin", 0.10))
        self.levels = tracked_levels() if self.enabled else {}
        self.kept = 0
        self.dropped = 0

    def accepts(self, symbol: str, strike: float | None, spot: float | None) -> bool:
        if not self.enabled:
            return True
        if strike is None:
            return True  # never drop a row just because the strike failed to parse
        levels = self.levels.get(symbol, set())
        if not levels and not spot:
            return True  # nothing to judge against: keep it rather than guess
        ok = keep_strike(strike, spot, levels, self.spot_band, self.level_margin)
        if ok:
            self.kept += 1
        else:
            self.dropped += 1
        return ok

    def summary(self) -> str:
        total = self.kept + self.dropped
        if not total:
            return "strike filter: nothing judged"
        return (f"strike filter kept {self.kept:,}/{total:,} "
                f"({100 * self.kept / total:.0f}%)")
