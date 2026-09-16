"""Deterministic parsing of Polymarket titles/questions into typed contracts.

Never guesses: anything that does not match a known shape is returned with
contract_type='unparsed' and a reason, so the run can log it for inspection.

Shapes seen in production (September 2026):
  close_above  "Will Tesla (TSLA) close above $290 end of September?"
               "Will Apple (AAPL) close above $320 on September 16?"
               "Will Apple (AAPL) finish week of September 14 above $300?"
  hit_up       "Will Apple (AAPL) hit (HIGH) $360 in September?"
               "Will S&P 500 (SPX) hit $9,000 (HIGH) in December?"
  hit_down     "Will Apple (AAPL) hit (LOW) $304 in September?"
  range        "Will Apple (AAPL) close at $310-$315 on the final day ...?"
               "... close at <$310 ...", "... close at >$355 ..."
  up_down      "Apple (AAPL) Up or Down on September 16?"  (outcomes Up/Down)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# A parenthesised ticker: AAPL, SPX, XAUUSD, BRK.B ...
TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)")

# A dollar amount, optionally with thousands separators and decimals.
NUM = r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)"

# Two live orderings: "hit (HIGH) $360" and "hit $7,800 (HIGH)".
HIT_QUAL_FIRST_RE = re.compile(r"\bhit\s*\((HIGH|LOW)\)\s*" + NUM, re.I)
HIT_NUM_FIRST_RE = re.compile(r"\bhit\s*" + NUM + r"\s*\((HIGH|LOW)\)", re.I)
CLOSE_ABOVE_RE = re.compile(r"clos(?:e|es)\s+above\s+" + NUM, re.I)
FINISH_WEEK_ABOVE_RE = re.compile(r"finish\s+week\s+of\s+.*?\babove\s*" + NUM, re.I)
CLOSE_BELOW_RE = re.compile(r"clos(?:e|es)\s+below\s+" + NUM, re.I)

RANGE_BAND_RE = re.compile(r"clos(?:e|es)\s+at\s+" + NUM + r"\s*[-–]\s*" + NUM, re.I)
RANGE_UNDER_RE = re.compile(r"clos(?:e|es)\s+at\s*<\s*" + NUM, re.I)
RANGE_OVER_RE = re.compile(r"clos(?:e|es)\s+at\s*>\s*" + NUM, re.I)

# Fallbacks against groupItemTitle, which is terser and always present on ladders.
GIT_ARROW_RE = re.compile(r"^\s*([↑↓])\s*" + NUM + r"\s*$")
GIT_BAND_RE = re.compile(r"^\s*" + NUM + r"\s*[-–]\s*" + NUM + r"\s*$")
GIT_UNDER_RE = re.compile(r"^\s*<\s*" + NUM + r"\s*$")
GIT_OVER_RE = re.compile(r"^\s*>\s*" + NUM + r"\s*$")
GIT_PLAIN_RE = re.compile(r"^\s*" + NUM + r"\s*$")

PERIOD_PATTERNS = [
    ("weekly", re.compile(r"finish\s+week|week\s+of|closes?\s+week", re.I)),
    ("monthly", re.compile(r"end\s+of\s+\w+|in\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)", re.I)),
    ("daily", re.compile(r"\bon\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}", re.I)),
]


def to_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


@dataclass
class ParsedMarket:
    contract_type: str          # close_above | hit_up | hit_down | up_down | range | unparsed
    ticker: str | None = None
    strike: float | None = None       # close_above threshold
    barrier: float | None = None      # hit_up / hit_down barrier level
    range_low: float | None = None
    range_high: float | None = None
    direction: str | None = None      # above | below | up | down | None
    period: str | None = None         # daily | weekly | monthly
    parse_reason: str | None = None   # why it failed, when unparsed

    def as_dict(self) -> dict:
        return asdict(self)


def parse_ticker(title: str) -> str | None:
    """Return the parenthesised ticker in a title, if any."""
    m = TICKER_RE.search(title or "")
    return m.group(1) if m else None


def parse_period(text: str) -> str | None:
    # Weekly wins over daily/monthly because weekly titles also name a month+day.
    for name, rx in PERIOD_PATTERNS:
        if rx.search(text or ""):
            return name
    return None


def parse_market(
    question: str,
    *,
    event_title: str = "",
    group_item_title: str | None = None,
    outcomes: list[str] | None = None,
) -> ParsedMarket:
    """Classify a single Polymarket market. Deterministic; never guesses a level."""
    q = (question or "").strip()
    combined = f"{event_title} {q}"
    ticker = parse_ticker(q) or parse_ticker(event_title)
    period = parse_period(q) or parse_period(event_title)

    def out(**kw) -> ParsedMarket:
        kw.setdefault("ticker", ticker)
        kw.setdefault("period", period)
        return ParsedMarket(**kw)

    # --- Up / Down: identified by its outcome labels, not by a level. ---
    norm_outcomes = [str(o).strip().lower() for o in (outcomes or [])]
    if set(norm_outcomes) == {"up", "down"} or re.search(r"\bup or down\b", combined, re.I):
        return out(contract_type="up_down", direction="up_down")

    # --- Hit / one-touch barriers. ---
    m = HIT_QUAL_FIRST_RE.search(q)
    qualifier, level = (m.group(1), m.group(2)) if m else (None, None)
    if not m:
        m = HIT_NUM_FIRST_RE.search(q)
        if m:
            qualifier, level = m.group(2), m.group(1)
    if m:
        level = to_float(level)
        if level is None:
            return out(contract_type="unparsed", parse_reason="hit level not numeric")
        is_high = qualifier.upper() == "HIGH"
        return out(
            contract_type="hit_up" if is_high else "hit_down",
            barrier=level,
            direction="above" if is_high else "below",
        )

    # --- Range buckets (check before close_above: they share the 'close' verb). ---
    m = RANGE_BAND_RE.search(q)
    if m:
        return out(contract_type="range", range_low=to_float(m.group(1)), range_high=to_float(m.group(2)))
    m = RANGE_UNDER_RE.search(q)
    if m:
        return out(contract_type="range", range_high=to_float(m.group(1)), direction="below")
    m = RANGE_OVER_RE.search(q)
    if m:
        return out(contract_type="range", range_low=to_float(m.group(1)), direction="above")

    # --- Close above / below. ---
    m = FINISH_WEEK_ABOVE_RE.search(q) or CLOSE_ABOVE_RE.search(q)
    if m:
        return out(contract_type="close_above", strike=to_float(m.group(1)), direction="above", period=period or "weekly" if "finish week" in q.lower() else period)
    m = CLOSE_BELOW_RE.search(q)
    if m:
        return out(contract_type="close_above", strike=to_float(m.group(1)), direction="below")

    # --- Fall back to groupItemTitle, using the event title for the contract type. ---
    git = (group_item_title or "").strip()
    if git:
        m = GIT_ARROW_RE.match(git)
        if m:
            up = m.group(1) == "↑"
            return out(
                contract_type="hit_up" if up else "hit_down",
                barrier=to_float(m.group(2)),
                direction="above" if up else "below",
            )
        m = GIT_BAND_RE.match(git)
        if m:
            return out(contract_type="range", range_low=to_float(m.group(1)), range_high=to_float(m.group(2)))
        m = GIT_UNDER_RE.match(git)
        if m:
            return out(contract_type="range", range_high=to_float(m.group(1)), direction="below")
        m = GIT_OVER_RE.match(git)
        if m:
            return out(contract_type="range", range_low=to_float(m.group(1)), direction="above")
        m = GIT_PLAIN_RE.match(git)
        if m and re.search(r"clos\w*\s+above|finish\s+week", event_title, re.I):
            return out(contract_type="close_above", strike=to_float(m.group(1)), direction="above")

    return out(contract_type="unparsed", parse_reason="no known pattern matched")
