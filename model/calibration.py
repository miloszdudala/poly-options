"""Price-versus-outcome calibration for Polymarket hit markets.

The question this file answers has no model in it: **do contracts priced at 5c
resolve YES 5% of the time?** FINDINGS section 5.3 lists it as the decisive
open question, and until the February-August 2026 history was backfilled
(`data/src/poly_backfill.py`) there was no sample to answer it with - every
market the collector had watched close resolved YES, because a touch closes a
hit market early and the NO outcomes only arrive at period end.

Three fair values are scored against the same outcomes:

* the **market price** itself (the hourly mid proxy from `prices-history`);
* the **empirical touch frequency** (MODELS 3.5): how often this ticker's
  regular-hours high/low historically reached `S * exp(+/- k * sigma * sqrt(h))`,
  with no option data and no Black-Scholes;
* the **continuous one-touch formula** (FINDINGS section 2) fed the same
  realised volatility. MODELS 3.3 expects this one to be biased high, because
  Polymarket only watches Pyth candles between 09:30 and 16:00 ET while the
  formula watches the price continuously.

Everything here is a pure function of arrays; loading and reporting live in
`run_calibration.py`.

Two rules from FINDINGS section 2 are structural, not stylistic:
never average `bid - fair` or `fair - ask` (both are negative by construction
whenever fair sits inside the spread), and key everything on `market_id`,
because `(ticker, barrier, contract_type)` repeats across expiries.
"""
from __future__ import annotations

import math

import numpy as np

# The barrier maths and the historical touch table moved to their MODELS-named
# homes when the engine was built (`fair.py` section 3.2-3.3, `empirical.py`
# section 3.5). They are re-exported here because this module's own vocabulary
# - a fair value, a realised vol, a touch table - is what the report speaks in,
# and because P1's report should keep working off one import.
from empirical import TRADING_DAYS, TouchTable, ewma_vol, touch_table  # noqa: F401
from fair import one_touch  # noqa: F401

# ------------------------------------------------------------------ buckets

# The boundaries FINDINGS 3.2 and 3.3 already use, so the calibration table and
# the displacement/cost tables can be read side by side.
PRICE_BUCKETS: list[tuple[float, float, str]] = [
    (0.00, 0.03, "<3c"),
    (0.03, 0.05, "3-5c"),
    (0.05, 0.10, "5-10c"),
    (0.10, 0.20, "10-20c"),
    (0.20, 0.35, "20-35c"),
    (0.35, 0.50, "35-50c"),
    (0.50, 0.65, "50-65c"),
    (0.65, 1.01, ">65c"),
]
BUCKET_ORDER = [label for _, _, label in PRICE_BUCKETS]

TIME_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 4.0, "<4h"),
    (4.0, 24.0, "4-24h"),
    (24.0, 72.0, "1-3d"),
    (72.0, 168.0, "3-7d"),
    (168.0, math.inf, ">7d"),
]
TIME_ORDER = [label for _, _, label in TIME_BUCKETS]


def price_bucket(price: float | None) -> str | None:
    """Label for a YES price. None for anything outside (0, 1]."""
    if price is None or not (0.0 < float(price) <= 1.0):
        return None
    p = float(price)
    for lo, hi, label in PRICE_BUCKETS:
        if lo <= p < hi:
            return label
    return None


def time_bucket(hours: float | None) -> str | None:
    """Label for hours remaining until the market's deadline."""
    if hours is None or not np.isfinite(hours) or hours < 0:
        return None
    for lo, hi, label in TIME_BUCKETS:
        if lo <= hours < hi:
            return label
    return None


def bucket_labels(prices) -> np.ndarray:
    """Vectorised `price_bucket` over an array."""
    p = np.asarray(prices, dtype=float)
    out = np.full(p.shape, None, dtype=object)
    valid = np.isfinite(p) & (p > 0.0) & (p <= 1.0)
    for lo, hi, label in PRICE_BUCKETS:
        out[valid & (p >= lo) & (p < hi)] = label
    return out


def time_bucket_labels(hours) -> np.ndarray:
    h = np.asarray(hours, dtype=float)
    out = np.full(h.shape, None, dtype=object)
    valid = np.isfinite(h) & (h >= 0)
    for lo, hi, label in TIME_BUCKETS:
        out[valid & (h >= lo) & (h < hi)] = label
    return out


# ------------------------------------------------------------- statistics


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Binomial 95% interval for a rate, Wilson score.

    Wilson rather than normal-approximation because most of the interesting
    buckets sit near 0 (a 3c contract that resolves YES twice in 900 tries),
    where the normal interval runs below zero and understates the width.
    """
    if trials <= 0:
        return (float("nan"), float("nan"))
    p = successes / trials
    denom = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def clustered_interval(sums, counts, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a rate whose observations are clustered by market.

    The reliability tables pool ~1.6M hourly points drawn from ~7k markets, and
    every point of one market shares that market's single outcome. A binomial
    interval treats them as 1.6M independent coin flips and comes out +/-0.001
    wide, which is a statement about the sample size and not about what is
    known. This is the ratio estimator's cluster-robust (sandwich) variance,
    with one cluster per market: `sums[m]` wins and `counts[m]` points.

    It is typically 10-30x wider than the binomial one, and it is the one to
    read.
    """
    s = np.asarray(sums, dtype=float)
    n = np.asarray(counts, dtype=float)
    total = n.sum()
    clusters = len(n)
    if total <= 0 or clusters < 2:
        return (float("nan"), float("nan"))
    rate = s.sum() / total
    resid = s - rate * n
    var = (clusters / (clusters - 1.0)) * float((resid ** 2).sum()) / (total ** 2)
    half = z * math.sqrt(max(var, 0.0))
    return (max(0.0, rate - half), min(1.0, rate + half))


def brier(probabilities, outcomes) -> float:
    """Mean squared error of a probability forecast. Lower is better.

    0.25 is the score of always saying 50%; the base rate of the sample is the
    score to beat for anything claiming to know something market-specific.
    """
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    keep = np.isfinite(p) & np.isfinite(y)
    if not keep.any():
        return float("nan")
    return float(np.mean((p[keep] - y[keep]) ** 2))


# --------------------------------------------------------------- the bet


HALF_SPREAD = 0.035   # half of the 7c median top-of-book spread, FINDINGS 3.3
FEE_RATE = 0.04       # taker fee, 0.04 * min(p, 1-p), MODELS section 2


def cost_to_buy(price, half_spread: float = HALF_SPREAD, fee_rate: float = FEE_RATE):
    """All-in cost per share of lifting a token quoted at mid `price`.

    The hourly history is a mid proxy, so a buyer crosses half the spread to
    reach the offer and then pays the taker fee on what they paid. Capped at 1:
    paying more than a dollar for a dollar payoff is never a fill, it is a
    broken quote.
    """
    p = np.clip(np.asarray(price, dtype=float) + half_spread, 0.0, 1.0)
    return p + fee_rate * np.minimum(p, 1.0 - p)


def leg_pnl(yes_price, resolved_yes, side: str, half_spread: float = HALF_SPREAD,
            fee_rate: float = FEE_RATE):
    """PnL per unit of buying one side at the quoted mid and holding to the end.

    `side` is "yes" or "no"; a NO token quoted at `1 - yes_price` (FINDINGS 3.3:
    a NO ask IS a YES bid, there is no separate vig). Payoff is 1 on the winning
    side, 0 on the other, so PnL is `payoff - cost`.
    """
    yes = np.asarray(yes_price, dtype=float)
    won = np.asarray(resolved_yes, dtype=bool)
    if side == "yes":
        cost = cost_to_buy(yes, half_spread, fee_rate)
        payoff = won.astype(float)
    elif side == "no":
        cost = cost_to_buy(1.0 - yes, half_spread, fee_rate)
        payoff = (~won).astype(float)
    else:
        raise ValueError(f"side must be 'yes' or 'no', not {side!r}")
    return payoff - cost, cost


def first_signal(mask) -> int | None:
    """Index of the first True in a time-ordered mask, else None.

    "One unit per market" means the bet is taken the first time the rule fires
    in that market's life, not once per hourly point - a rule that fires for 40
    straight hours is one opportunity, and counting it 40 times would multiply
    a single outcome into a fake sample.
    """
    hit = np.asarray(mask, dtype=bool)
    if not hit.any():
        return None
    return int(np.argmax(hit))
