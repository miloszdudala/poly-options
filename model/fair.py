"""The shared fair-value engine: implied vol, and the probability of a touch.

MODELS section 3. Every model calls this; they differ in which fair they trust
and how they size, never in how the number is computed.

The engine's one substantive claim is that **the continuous one-touch formula
is wrong for this product**, and by how much. Polymarket resolves a hit market
on Pyth 1-minute candles between 09:30 and 16:00 ET. A barrier breached at
02:00 and recovered by the open never happened; an overnight gap that opens
beyond the barrier did. The closed form in FINDINGS section 2 watches the price
continuously and so counts the first case, which is why it is carried here as
`fair_cont` - an upper bound to compare against, not a price.

`touch_probability` models the path the way the venue sees it: per session, an
unobserved overnight gap carrying `(1-f)` of the variance, then an observed
session carrying `f` of it, with the session's interior handled by the Brownian
bridge rather than by more sampling.

Three quantities, three failure modes, all flagged rather than hidden:

* `sigma_K` (implied vol at the barrier strike) is the right vol for a barrier
  and the wrong one for a deep-OTM barrier, where feeding one smile point into
  a constant-vol formula double-counts skew: FINDINGS 5.2 measured a SPY
  barrier whose fair ran 0.0018 to 0.1466 - 80x - depending on which vol went
  in. Flagged `deep_otm`.
* `sigma_atm` is stable and ignores the skew that a barrier is mostly about.
  Both are computed; MODELS 5.1 trades only what is positive under both.
* The chain is 15 minutes older than the book. `quote_age_s` carries it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sessions import TRADING_DAYS, sessions_remaining

SQRT_2 = math.sqrt(2.0)


def norm_cdf(x):
    """Standard normal CDF, vectorised over numpy arrays.

    `math.erf` under `np.vectorize` rather than `scipy.special.ndtr`: one
    function is not worth a dependency, and this is not in the inner loop -
    the Monte Carlo uses normals, not their CDF.
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
    touch model runs on, where a weekend carries no variance. What passes
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


def pick_expiries(expiry_closes: list, deadline) -> tuple:
    """The nearest expiry on-or-after the deadline, and the nearest before it.

    MODELS 3.2. Compared at the expiry's 16:00 ET close, not at midnight on its
    date: a Friday weekly barrier deadlines at that Friday's close, which is the
    Friday expiry's own moment, so comparing dates alone files the one expiry
    that matches the payoff exactly on the wrong side.

    Either may be missing, and the caller flags that rather than inventing one.
    """
    after = sorted(e for e in expiry_closes if e >= deadline)
    before = sorted(e for e in expiry_closes if e < deadline)
    return (after[0] if after else None), (before[-1] if before else None)


def interp_vol(s0: Smile | None, s1: Smile | None, model_years: float, x: float
               ) -> tuple[float, bool, str]:
    """Model-clock vol at log-moneyness `x` and horizon `model_years`.

    MODELS 3.2: interpolate **total variance** linearly in time, never vol.
    Interpolating vol directly puts a kink at every expiry and prices a
    two-day barrier off a curve that bends where no option trades.

    The interpolation axis is session time, because that is the clock the
    barrier is monitored on; the quantity interpolated is the clock-free total
    variance. Returns `(sigma_model, clamped, source)`.
    """
    usable = [s for s in (s0, s1) if s is not None and len(s.vols)]
    if not usable:
        return float("nan"), False, "none"
    if model_years <= 0:
        return float("nan"), False, "expired"
    if len(usable) == 1:
        w, clamped = usable[0].total_variance_at(x)
        return float(math.sqrt(max(w, 0.0) / model_years)), clamped, "single_expiry"
    a, b = sorted(usable, key=lambda s: s.years_model)
    wa, ca = a.total_variance_at(x)
    wb, cb = b.total_variance_at(x)
    if not (np.isfinite(wa) and np.isfinite(wb)):
        good = b if np.isfinite(wb) else a
        w, clamped = good.total_variance_at(x)
        return float(math.sqrt(max(w, 0.0) / model_years)), clamped, "single_expiry"
    if b.years_model <= a.years_model:
        return float(math.sqrt(max(wa, 0.0) / model_years)), ca, "degenerate_expiries"
    t = (model_years - a.years_model) / (b.years_model - a.years_model)
    w = wa + (wb - wa) * t
    return float(math.sqrt(max(w, 0.0) / model_years)), bool(ca or cb), "interpolated"


def flat_vol(smile: "Smile", x: float) -> tuple[float, bool, str]:
    """The smile's own vol at log-moneyness `x`, extrapolated flat in vol.

    For the earnings gap (MODELS 3.4, review 2026-09-18): an earnings report
    between the barrier's deadline and the expiry after it puts a jump into
    that expiry's variance which the barrier never sees. Interpolating toward
    it lends the barrier part of the jump; the pre-earnings expiry's vol,
    carried flat to the deadline, does not. `sqrt(w / years)` on the expiry's
    own clock, independent of the deadline's horizon.
    """
    w, clamped = smile.total_variance_at(x)
    if not (np.isfinite(w) and smile.years_model > 0):
        return float("nan"), False, "none"
    return float(math.sqrt(max(w, 0.0) / smile.years_model)), clamped, "pre_earnings_flat"


# --------------------------------------------------- continuous one-touch


def one_touch(spot, barrier, years, sigma, rate=0.0, direction="up"):
    """P(the price touches `barrier` before `years`), continuous monitoring.

    FINDINGS section 2, with the down barrier obtained by reflection: the log
    price has drift `m = r - sigma^2/2`, and a down barrier is an up barrier on
    the reflected path, so `b -> ln(S/B)` and `m -> -m`.

    Carried as `fair_cont`: MODELS 3.3's upper bound, not a price.
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


# ------------------------------------------- regular-hours touch, MODELS 3.3


DEFAULT_PATHS = 20_000


def normal_pool(max_legs: int, paths: int = DEFAULT_PATHS, seed: int = 20260916) -> np.ndarray:
    """Antithetic standard normals, shape (paths, max_legs, 2), generated once.

    Reused for every market and every delta bump in a cycle. Sharing them makes
    the run reproducible and, more importantly, makes the delta bump a
    common-random-numbers difference: at 20k paths the MC standard error is
    ~0.0035, which on its own would swamp the effect of a 0.5% spot bump
    entirely. The two halves are exact negatives of each other, so any odd
    function of the draws has zero sampling error in the mean.
    """
    rng = np.random.default_rng(seed)
    half = rng.standard_normal((paths // 2, max_legs, 2))
    return np.concatenate([half, -half], axis=0)


def _legs(sessions_left: float) -> list[tuple[float, bool]]:
    """Split the horizon into (session fraction, has an overnight gap) legs.

    A fractional result from `sessions_remaining` means the cycle is inside a
    session, so the first leg is the rest of today with no gap in front of it.
    An integer means the cycle is outside one, so every session ahead - the
    next one included - opens with a gap.
    """
    n_full = int(math.floor(sessions_left + 1e-9))
    frac = sessions_left - n_full
    legs: list[tuple[float, bool]] = []
    if frac > 1e-9:
        legs.append((frac, False))
    legs += [(1.0, True)] * n_full
    return legs


def touch_probability(spot: float, barrier: float, sessions_left: float, sigma: float,
                      rate: float, f: float, is_up: bool, pool: np.ndarray) -> float:
    """P(touch during regular hours only), MODELS 3.3, by Monte Carlo.

    Per session: an unobserved overnight gap carrying `(1-f)` of the variance,
    then an observed session carrying `f` of it. A touch happens if any open or
    close is beyond the barrier, or if the Brownian bridge between an open and
    its close crosses it:

        P(cross | O, C) = exp(-2 * (b - o) * (b - c) / v)

    in log space, with `v` the session's variance. Note the expression is the
    same for an up and a down barrier - `(b-o)(b-c)` is symmetric under
    reflection - so there is one code path and no sign to get backwards.

    The bridge probability is multiplied into a survival factor rather than
    turned into a coin flip. That is conditional Monte Carlo: it integrates the
    within-session crossing exactly instead of sampling it, and it cuts the
    variance by roughly an order of magnitude for the same number of paths.
    """
    legs = _legs(sessions_left)
    if len(legs) > pool.shape[1]:
        raise ValueError(f"normal pool holds {pool.shape[1]} legs, this market needs "
                         f"{len(legs)}; size the pool from the cycle's longest horizon")
    if not legs or sigma <= 0 or spot <= 0 or barrier <= 0:
        beyond_now = (spot >= barrier) if is_up else (spot <= barrier)
        return 1.0 if beyond_now else 0.0

    b = math.log(barrier)
    sgn = 1.0 if is_up else -1.0
    if sgn * (math.log(spot) - b) >= 0:
        return 1.0                      # already beyond: the touch is pending

    dt = 1.0 / TRADING_DAYS
    mu = rate - 0.5 * sigma ** 2
    var_day = sigma ** 2 * dt

    paths = pool.shape[0]
    log_s = np.full(paths, math.log(spot))
    survive = np.ones(paths)
    alive = np.ones(paths, dtype=bool)

    for i, (share, has_gap) in enumerate(legs):
        z_gap, z_day = pool[:, i, 0], pool[:, i, 1]
        if has_gap:
            var_gap = (1.0 - f) * var_day
            log_o = log_s + (1.0 - f) * mu * dt + math.sqrt(max(var_gap, 0.0)) * z_gap
        else:
            log_o = log_s
        alive &= (sgn * (log_o - b) < 0)          # the gap opened through it
        var_day_leg = f * var_day * share
        log_c = log_o + f * mu * dt * share + math.sqrt(max(var_day_leg, 0.0)) * z_day
        alive &= (sgn * (log_c - b) < 0)
        if var_day_leg > 0:
            cross = np.exp(np.clip(-2.0 * (b - log_o) * (b - log_c) / var_day_leg, -700, 0.0))
            survive *= np.where(alive, 1.0 - cross, 0.0)
        else:
            survive *= alive                       # f = 0: nothing inside the session
        log_s = log_c
        if not alive.any():
            break
    survive = np.where(alive, survive, 0.0)
    return float(1.0 - survive.mean())


def touch_delta(spot: float, barrier: float, sessions_left: float, sigma: float,
                rate: float, f: float, is_up: bool, pool: np.ndarray,
                bump: float = 0.005) -> float:
    """`dP/dS` by central difference on a 0.5% spot bump, MODELS 5.2.

    Both legs use the same draws, so what is differenced is one function of one
    sample rather than two noisy estimates. A YES on `hit_down` has negative
    delta and a NO on `hit_down` positive; this returns the YES-side number and
    the caller flips it.
    """
    up_spot, down_spot = spot * (1.0 + bump), spot * (1.0 - bump)
    p_up = touch_probability(up_spot, barrier, sessions_left, sigma, rate, f, is_up, pool)
    p_down = touch_probability(down_spot, barrier, sessions_left, sigma, rate, f, is_up, pool)
    return (p_up - p_down) / (up_spot - down_spot)


# ------------------------------------------------------------------- flags


@dataclass
class Flags:
    """MODELS 3.4. Every fair value carries the reasons not to believe it."""

    deep_otm: bool = False
    clamped_strike: bool = False
    clamped_atm: bool = False
    single_expiry: bool = False
    exact_expiry: bool = False
    no_chain: bool = False
    stale_book: bool = False
    earnings_inside: bool = False
    earnings_gap: bool = False
    touch_pending: bool = False
    names: list = field(default_factory=list)

    def set(self, name: str, value: bool = True) -> None:
        setattr(self, name, bool(value))

    def as_string(self) -> str:
        on = [n for n in ("deep_otm", "clamped_strike", "clamped_atm", "single_expiry",
                          "exact_expiry", "no_chain", "stale_book", "earnings_inside",
                          "earnings_gap", "touch_pending")
              if getattr(self, n)]
        return ",".join(on)


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
