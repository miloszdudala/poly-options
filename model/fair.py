"""The shared fair-value maths: implied vol, and the probability of a touch.

Every model calls this; they differ in how they size, never in how the number
is computed.

A hit market's fair is the continuous one-touch formula (`one_touch`) with the
at-the-money implied vol of the first expiry at or after the deadline
(CLAUDE.md, Decided). Polymarket resolves on regular-hours candles only, so
continuous monitoring counts some touches the venue does not; the correction
was measured and not adopted (see the comment in `engine.price_cycle`).

Reasons not to believe a fair are flagged, not hidden (`Flags`). The chain is
15 minutes older than the book; `quote_age_s` carries it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numpy as np
import pandas as pd


SQRT_2 = math.sqrt(2.0)


def norm_cdf(x):
    """Standard normal CDF, vectorised over numpy arrays.

    `math.erf` under `np.vectorize` rather than `scipy.special.ndtr`: one
    function is not worth a dependency, and this is not in the inner loop -
    nothing here calls it in a tight loop.
    """
    x = np.asarray(x, dtype=float)
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / SQRT_2))


# ------------------------------------------------------------ Black-Scholes


def bs_price(spot, strike, years, sigma, rate=0.0, is_call=True):
    """European option price, no dividend.

    Dividends are deliberately omitted: the names traded here pay little or
    nothing over a two-day to one-month horizon, and `dividends` is collected
    so that decision can be revisited with a number rather than a shrug.
    """
    S = np.asarray(spot, dtype=float)
    K = np.asarray(strike, dtype=float)
    T = np.asarray(years, dtype=float)
    sig = np.asarray(sigma, dtype=float)
    r = np.asarray(rate, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_t = sig * np.sqrt(T)
        d1 = (np.log(S / K) + (r + 0.5 * sig ** 2) * T) / vol_t
        d2 = d1 - vol_t
        call = S * norm_cdf(d1) - K * np.exp(-r * T) * norm_cdf(d2)
        put = K * np.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)
    intrinsic_call = np.maximum(S - K * np.exp(-r * T), 0.0)
    intrinsic_put = np.maximum(K * np.exp(-r * T) - S, 0.0)
    priced = np.where(is_call, call, put)
    floor = np.where(is_call, intrinsic_call, intrinsic_put)
    return np.where((T <= 0) | (sig <= 0), floor, priced)


def implied_vol(price, spot, strike, years, rate=0.0, is_call=True,
                lo=1e-4, hi=5.0, iterations=80):
    """Invert Black-Scholes by bisection, vectorised.

    Bisection rather than Newton: vega collapses on the deep wings that this
    dataset is full of, and a Newton step there walks off to nonsense silently.
    Bisection cannot, because the price is monotone in sigma. 80 halvings of
    [1e-4, 5] resolve sigma to 4e-23, which is well past the point where the
    input quote means anything.

    Returns NaN where the quote is outside the no-arbitrage range, which is
    where a placeholder quote on an untraded contract usually lands
    (FINDINGS section 4).
    """
    price = np.asarray(price, dtype=float)
    S = np.asarray(spot, dtype=float)
    K = np.asarray(strike, dtype=float)
    T = np.asarray(years, dtype=float)
    r = np.asarray(rate, dtype=float)
    call = np.asarray(is_call)

    low = np.full(price.shape, float(lo))
    high = np.full(price.shape, float(hi))
    p_low = bs_price(S, K, T, low, r, call)
    p_high = bs_price(S, K, T, high, r, call)
    bracketed = np.isfinite(price) & (price >= p_low - 1e-12) & (price <= p_high + 1e-12) & (T > 0)

    for _ in range(iterations):
        mid = 0.5 * (low + high)
        too_cheap = bs_price(S, K, T, mid, r, call) < price
        low = np.where(too_cheap, mid, low)
        high = np.where(too_cheap, high, mid)
    return np.where(bracketed, 0.5 * (low + high), np.nan)


# ------------------------------------------------------------------ smiles


@dataclass
class Smile:
    """One expiry's OTM implied-vol curve in log-moneyness.

    Two clocks, and the difference is not cosmetic. **`years_cal`** is calendar
    time from the quote to the expiry's 16:00 ET close, which is the clock the
    quoted vol was computed on - measured 2026-09-16 against Cboe's own
    `source_iv`, that convention reproduces it to a median of 0.003-0.009 vol
    points across four expiries, while trading-day conventions are out by
    0.04-0.11 on the near expiry. Inverting on any other clock and then
    comparing to `source_iv` compares two different definitions and finds a
    disagreement that is not there.

    **`years_model`** is the same expiry in NYSE session time, the clock the
    touch formula runs on, where a weekend carries no variance. What passes
    between them is **total variance** `w = sigma^2 * T`, which is a property of
    the quote and not of the clock. So: invert on the calendar clock, carry `w`,
    divide by session time at the end.
    """

    expiry: pd.Timestamp
    years_cal: float
    years_model: float
    log_moneyness: np.ndarray       # ln(K / S_quote), ascending
    vols: np.ndarray                # calendar-clock implied vol
    n_contracts: int

    def vol_at(self, x: float) -> tuple[float, bool]:
        """Calendar-clock vol at log-moneyness `x`, clamped to the liquid range.

        Returns `(vol, clamped)`. Clamping is flat extrapolation, which is the
        conservative choice: the alternative, extending the smile's slope, is
        how a deep-OTM barrier ends up priced at 80x its neighbour
        (FINDINGS 5.2).
        """
        if len(self.vols) == 0:
            return float("nan"), False
        lo, hi = self.log_moneyness[0], self.log_moneyness[-1]
        clamped = bool(x < lo or x > hi)
        return float(np.interp(np.clip(x, lo, hi), self.log_moneyness, self.vols)), clamped

    def total_variance_at(self, x: float) -> tuple[float, bool]:
        """`sigma^2 * T` at log-moneyness `x`: the clock-free quantity."""
        vol, clamped = self.vol_at(x)
        return vol ** 2 * self.years_cal, clamped


def build_smile(chain: pd.DataFrame, spot: float, expiry, years_cal: float,
                years_model: float, rate: float = 0.0,
                log: "callable | None" = None) -> Smile:
    """OTM contracts of one expiry, with IV recomputed from the bid/ask mid.

    MODELS 3.2: puts below spot, calls above, IV from the mid rather than from
    `source_iv`. Cboe's own IV is good - FINDINGS section 4 verified it to
    within 0.008 by hand-inverting Black-Scholes - but "good on the strikes we
    checked" is not "trusted blindly", so disagreements past 0.01 are logged
    and the recomputed number is the one used.
    """
    rows = chain[chain["expiry"] == expiry]
    otm = rows[((rows["option_type"] == "put") & (rows["strike"] < spot))
               | ((rows["option_type"] == "call") & (rows["strike"] >= spot))]
    if otm.empty or not (np.isfinite(years_cal) and years_cal > 0):
        return Smile(expiry, years_cal, years_model, np.array([]), np.array([]), 0)

    vols = implied_vol(otm["mid"].values, spot, otm["strike"].values, years_cal, rate,
                       (otm["option_type"] == "call").values)
    if log is not None:
        src = otm["source_iv"].values.astype(float)
        liquid = otm["open_interest"].values >= 100
        gap = np.abs(vols - src)
        keep = liquid & np.isfinite(gap)
        if keep.any():
            log(otm.loc[keep, "contract_symbol"].tolist(), vols[keep], src[keep])

    keep = np.isfinite(vols) & (vols > 1e-3) & (vols < 4.99)
    if not keep.any():
        return Smile(expiry, years_cal, years_model, np.array([]), np.array([]), 0)
    x = np.log(otm["strike"].values[keep] / spot)
    order = np.argsort(x)
    return Smile(expiry, years_cal, years_model, x[order], vols[keep][order], int(keep.sum()))


def first_expiry_at_or_after(expiry_closes: list, deadline):
    """The nearest expiry close on or after the deadline, or None.

    Compared at the expiry's 16:00 ET close, not at midnight on its date: a
    Friday weekly barrier deadlines at that Friday's close, which is the Friday
    expiry's own moment, so comparing dates alone would skip the one expiry
    that matches the payoff exactly.
    """
    after = sorted(e for e in expiry_closes if e >= deadline)
    return after[0] if after else None


def expiry_vol(smile: "Smile", x: float) -> tuple[float, bool]:
    """The smile's vol at log-moneyness `x`, on the session clock.

    `sqrt(w / years_model)` with `w` the expiry's total variance: the quoted
    vol moved from the calendar clock to the session clock the touch formula
    runs on. Returns `(sigma, clamped)`.
    """
    w, clamped = smile.total_variance_at(x)
    if not (np.isfinite(w) and smile.years_model > 0):
        return float("nan"), False
    return float(math.sqrt(max(w, 0.0) / smile.years_model)), clamped


# --------------------------------------------------- continuous one-touch


def one_touch(spot, barrier, years, sigma, rate=0.0, direction="up"):
    """P(the price touches `barrier` before `years`), continuous monitoring.

    FINDINGS section 2, with the down barrier obtained by reflection: the log
    price has drift `m = r - sigma^2/2`, and a down barrier is an up barrier on
    the reflected path, so `b -> ln(S/B)` and `m -> -m`.
    """
    S = np.asarray(spot, dtype=float)
    B = np.asarray(barrier, dtype=float)
    T = np.asarray(years, dtype=float)
    sig = np.asarray(sigma, dtype=float)
    r = np.asarray(rate, dtype=float)

    up = np.asarray(direction) == "up" if not isinstance(direction, str) else direction == "up"
    m = r - 0.5 * sig ** 2
    b = np.where(up, np.log(B / S), np.log(S / B))
    m = np.where(up, m, -m)

    with np.errstate(divide="ignore", invalid="ignore"):
        vol_t = sig * np.sqrt(T)
        first = norm_cdf((m * T - b) / vol_t)
        second = np.exp(np.clip(2.0 * m * b / sig ** 2, -700, 700)) * norm_cdf((-b - m * T) / vol_t)
        out = first + second
    out = np.where(b <= 0, 1.0, out)
    out = np.where((T <= 0) | (sig <= 0), np.where(b <= 0, 1.0, 0.0), out)
    return np.clip(out, 0.0, 1.0)


def one_touch_delta(spot: float, barrier: float, years: float, sigma: float,
                    rate: float, direction: str, bump: float = 0.005) -> float:
    """`dP/dS` of `one_touch`, by central difference on a 0.5% spot bump.

    The YES-side number: negative for a down barrier. The caller flips it for
    a NO.
    """
    up_spot, down_spot = spot * (1.0 + bump), spot * (1.0 - bump)
    p_up = float(one_touch(up_spot, barrier, years, sigma, rate, direction))
    p_down = float(one_touch(down_spot, barrier, years, sigma, rate, direction))
    return (p_up - p_down) / (up_spot - down_spot)


# ------------------------------------------------------------------- flags


@dataclass
class Flags:
    """MODELS 3.4. Every fair value carries the reasons not to believe it."""

    deep_otm: bool = False
    clamped_atm: bool = False
    exact_expiry: bool = False
    no_chain: bool = False
    stale_book: bool = False
    earnings_inside: bool = False
    earnings_gap: bool = False
    touch_pending: bool = False

    @classmethod
    def names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def set(self, name: str, value: bool = True) -> None:
        if name not in self.names():
            raise AttributeError(f"no flag called {name!r}")
        setattr(self, name, bool(value))

    def as_string(self) -> str:
        return ",".join(n for n in self.names() if getattr(self, n))

    def columns(self) -> dict:
        """One boolean column per flag, plus `flags`, the ones set, joined."""
        return {**{n: getattr(self, n) for n in self.names()}, "flags": self.as_string()}


def deep_otm(spot: float, barrier: float, sigma_atm: float, years: float,
             threshold: float = 2.5) -> bool:
    """MODELS 3.4: the barrier is more than 2.5 standard moves away.

    Past this the fair value is a statement about which smile point was picked,
    not about the market: FINDINGS 5.2 measured 0.0018 against 0.1466 on one
    SPY barrier depending on the vol input, an 80x spread. Models must not
    trade these on the option signal.
    """
    if not (np.isfinite(sigma_atm) and sigma_atm > 0 and years > 0):
        return True
    return bool(abs(math.log(barrier / spot)) / (sigma_atm * math.sqrt(years)) > threshold)
