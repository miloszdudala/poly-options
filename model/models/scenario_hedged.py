"""`scenario_hedged`: keep the book's risk small, measured the way it is.

**The rule.** Every bet that clears the bar goes in, best ROI first, at the
largest size that keeps the whole book inside two limits:

* its **standard deviation** is at most `sd_frac` (8%) of what is held;
* in **no scenario** does its expected PnL lose more than `loss_frac` (10%).

($50 floors on both while the book is small.) A book that has drifted outside
a limit (a side resolved, a market touched, the clock ran down) may not drift
further, and while it is outside, a bet that brings it back may come in at a
lower bar, EV >= 0.

**The risk model** (measured 2026-10-19 on every market priced 09-16 .. 10-16,
and on every model's settled bets; MODELS 5.2d has the numbers):

* *How much stocks move, against what options priced.* Touches came in at
  0.72x the model's rate in the week of 09-21 and 1.59x in the week of 10-12.
  Modelled as a volatility multiplier `k` shared by every ticker, log-sd 0.21.
  A calm week pays every NO at once, above the price and below it.
* *Which way the market drifts*, `mu` implied sigmas, sd 0.39.
* *The ticker.* Bets on one ticker, type and side (four EWY hit_up NO at
  188/192/193/194) win or lose together: one coin flip, not four.

Scenarios are 7 values of `k` (out to +-3 log-sd) times 7 of `mu` (+-3 sd),
weighted by a normal density on each axis. A bet's win probability in a
scenario comes from its own model probability `q`: a touch has `q = 2 N(-a)`
for a barrier `a` sigmas away, so in the scenario it is `2 N(-(a -+ mu) / k)`
(minus `mu` for an up barrier, plus for a down one), which is `q` again at
`k = 1, mu = 0`. The book's variance is the variance of its expected PnL
across scenarios plus, per ticker x type x side, `(sum of shares x
sqrt(p (1 - p)))^2`. Both factor sizes were fitted on the same five weeks
the model is scored on: re-fit them on a longer history.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np

from models.bets import YES, Bet, Holding, Model
from sim import Params

LOSS_FRAC = 0.10
SD_FRAC = 0.08
VOL_LOG_SD = 0.21
DRIFT_SD = 0.39

# ------------------------------------------------------------- the scenarios

_N = NormalDist()
_cdf = np.vectorize(_N.cdf)
_GRID = np.arange(-3.0, 4.0)


def _axis_weights(grid: np.ndarray) -> np.ndarray:
    """A normal density on the grid, times the width each point stands for."""
    mids = (grid[1:] + grid[:-1]) / 2
    cdf = np.concatenate([[0.0], [_N.cdf(m) for m in mids], [1.0]])
    return np.diff(cdf)


K = np.repeat(np.exp(_GRID * VOL_LOG_SD), len(_GRID))      # vol multiplier per scenario
MU = np.tile(_GRID * DRIFT_SD, len(_GRID))                  # drift per scenario
WEIGHTS = np.outer(_axis_weights(_GRID), _axis_weights(_GRID)).ravel()


def touch_probs(q_touch: float, up: bool) -> np.ndarray:
    """Touch probability in every scenario, from the model's own `q_touch`.

    Worked example: the model says a barrier has a 30% chance of being
    touched. For a random walk, P(touch) = 2 N(-a) where `a` is the distance
    to the barrier in sigmas, so a = -N^-1(0.15) = 1.04 sigmas.
      * calm scenario, k = 0.53 (moves half what options priced): the barrier
        is effectively 1.04 / 0.53 = 1.96 sigmas away -> 2 N(-1.96) = 5%;
      * wild scenario, k = 1.88: 1.04 / 1.88 = 0.55 sigmas -> 58%;
      * a rally of mu = +0.39 sigmas brings an up barrier closer (a - mu)
        and pushes a down barrier away (a + mu).
    """
    q = min(max(q_touch, 1e-6), 1.0 - 1e-6) if np.isfinite(q_touch) else 0.5
    a = -_N.inv_cdf(q / 2.0)                      # distance to the barrier, sigmas
    dist = (a - MU) if up else (a + MU)           # after the drift, per scenario
    # A drift past the barrier means it was crossed: distance 0, probability 1.
    return np.clip(2.0 * _cdf(-np.maximum(dist, 0.0) / K), 0.0, 1.0)


def win_probs(q_touch: float, kind: str, side: str) -> np.ndarray:
    """This side's win probability in every scenario."""
    t = touch_probs(q_touch, kind == "hit_up")
    return t if side == YES else 1.0 - t


# ------------------------------------------------------------------ the book


@dataclass(frozen=True)
class Book:
    """What the limits look at.

    `pnl` - the book's expected PnL in each scenario; `clusters` - per ticker
    x type x side, the summed coin-flip spread `shares x sqrt(p (1 - p))`;
    `held` - dollars paid for everything in it.
    """

    pnl: np.ndarray = field(default_factory=lambda: np.zeros(len(WEIGHTS)))
    clusters: dict = field(default_factory=dict)
    held: float = 0.0

    def plus(self, cluster: tuple, shares: float, cost: float, wins: np.ndarray,
             p: float, t: float = 1.0) -> "Book":
        """The book with `t` times this bet added."""
        clusters = dict(self.clusters)
        clusters[cluster] = clusters.get(cluster, 0.0) + t * shares * math.sqrt(p * (1.0 - p))
        return Book(self.pnl + t * (shares * wins - cost), clusters, self.held + t * cost)

    def plus_settled(self, payout: float, cost: float) -> "Book":
        """The book with a bet whose outcome is already known."""
        return Book(self.pnl + payout - cost, self.clusters, self.held + cost)

    def sd(self) -> float:
        """Standard deviation of the book's PnL, in dollars.

        Two parts, added as variances:
          * across - how much the expected PnL differs between scenarios
            (the shared risk: a calm week, a wild week, a rally, a sell-off);
          * within - the coin flips left once the scenario is known. Bets in
            one cluster (ticker x type x side) move together, so their spreads
            add before squaring; different clusters add after squaring.
        """
        mean = WEIGHTS @ self.pnl
        across = WEIGHTS @ (self.pnl - mean) ** 2
        within = sum(c * c for c in self.clusters.values())
        return math.sqrt(across + within)

    def worst(self) -> float:
        return float(self.pnl.min())


def cluster_of(ticker: str, kind: str, side: str) -> tuple:
    return (ticker, kind, side)


def book_of(holdings: list[Holding]) -> Book:
    book = Book()
    for h in holdings:
        if h.touched:      # decided: a YES has won, a NO has lost
            book = book.plus_settled(h.shares if h.side == YES else 0.0, h.cost)
            continue
        wins = win_probs(h.touch_prob, h.kind, h.side)
        book = book.plus(cluster_of(h.ticker, h.kind, h.side), h.shares, h.cost,
                         wins, float(wins @ WEIGHTS))
    return book


# ----------------------------------------------------------------- the model


class ScenarioHedged(Model):
    name = "scenario_hedged"
    params = Params(ticker_cap_frac=0.12, direction_cap_frac=1.0)

    def __init__(self, min_ev: float = 0.03, min_roi: float = 0.08,
                 loss_budget: float = 50.0, loss_frac: float = LOSS_FRAC,
                 sd_budget: float = 50.0, sd_frac: float | None = SD_FRAC,
                 hedge_min_ev: float | None = 0.0):
        super().__init__(min_ev, min_roi)
        self.loss_budget = loss_budget
        self.loss_frac = loss_frac
        self.sd_budget = sd_budget
        self.sd_frac = sd_frac
        self.hedge_min_ev = hedge_min_ev

    # The limits, in dollars, for a book holding `held`.
    def budget(self, held: float) -> float:
        return max(self.loss_budget, self.loss_frac * held)

    def sd_limit(self, held: float) -> float:
        return max(self.sd_budget, self.sd_frac * held)

    def violation(self, book: Book) -> float:
        """Dollars by which the book is outside its limits; 0 inside them."""
        out = max(0.0, -book.worst() - self.budget(book.held))
        if self.sd_frac is not None:
            out += max(0.0, book.sd() - self.sd_limit(book.held))
        return out

    def decide(self, offers: list[Bet]) -> None:
        book = book_of(self.holdings)           # risk of what is already held
        minimum = self.params.min_fill_usd      # Polymarket's $1 order minimum
        chosen = set()                          # bets taken in pass 1

        # 1. Edges: each at the largest size that leaves the book no further
        #    outside its limits than it already is.
        for bet in offers:
            if not bet.clears(self.min_ev, self.min_roi):
                continue
            limit = bet.limit(self.min_ev, self.min_roi)
            usd = self.cap(bet)
            got = bet.quote(usd, limit)         # what the book would fill at full size
            if got.cost < minimum:
                self.refuse("no_depth")
                continue
            cluster = cluster_of(bet.ticker, bet.kind, bet.side)
            wins = win_probs(bet.touch_prob, bet.kind, bet.side)    # per scenario
            p = float(wins @ WEIGHTS)                               # overall
            # How much of the full size fits: 1 = all of it, 0.3 = 30%.
            t = self._largest_fraction(book, cluster, got.shares, got.cost, wins, p)
            if t < 1.0:
                # Re-quote the smaller size (the average price changes with
                # size) and check the book is really no worse off.
                usd = got.cost * t
                if usd < minimum:
                    self.refuse("risk_limit")
                    continue
                got = bet.quote(usd, limit)
                after = book.plus(cluster, got.shares, got.cost, wins, p)
                if got.cost < minimum or self.violation(after) > self.violation(book) + 1e-6:
                    self.refuse("risk_limit")
                    continue
            self.take_bet(bet, usd=usd, reason="scenario_hedged", limit=limit)
            book = book.plus(cluster, got.shares, got.cost, wins, p)
            chosen.add((bet.market_id, bet.side))

        # 2. Hedges: while the book is outside its limits, bets at the lower
        #    bar that bring it back, each at the size that helps most.
        if self.hedge_min_ev is None or self.violation(book) <= 0:
            return
        for bet in offers:
            before = self.violation(book)
            if before <= 0:
                break
            if not bet.clears(self.hedge_min_ev, 0.0) or (bet.market_id, bet.side) in chosen:
                continue
            limit = bet.limit(self.hedge_min_ev, 0.0)
            usd = self.cap(bet)
            got = bet.quote(usd, limit)
            if got.cost < minimum:
                continue
            cluster = cluster_of(bet.ticker, bet.kind, bet.side)
            wins = win_probs(bet.touch_prob, bet.kind, bet.side)
            p = float(wins @ WEIGHTS)
            # Too big a hedge overshoots and adds risk on the other side.
            sizes = np.linspace(0.05, 1.0, 20)
            outcome = [self.violation(book.plus(cluster, got.shares, got.cost, wins, p, t))
                       for t in sizes]
            t = float(sizes[int(np.argmin(outcome))])
            if min(outcome) >= before - 1e-6 or t * got.cost < minimum:
                continue
            if t < 1.0:
                usd = got.cost * t
                got = bet.quote(usd, limit)
            after = book.plus(cluster, got.shares, got.cost, wins, p)
            if self.violation(after) >= before - 1e-6:
                continue
            self.take_bet(bet, usd=usd, reason="scenario_hedged_hedge", limit=limit)
            book = after
            self.count("hedges")

    def _largest_fraction(self, book: Book, cluster: tuple, shares: float, cost: float,
                          wins: np.ndarray, p: float) -> float:
        """Largest t in [0, 1] whose book is no further outside the limits than `book`.

        By bisection, which assumes the limit is crossed once between 0 and 1
        (the worst loss and the standard deviation are convex in t, the limits
        close to linear); `decide` re-checks the size it settles on.
        """
        allowed = self.violation(book) + 1e-9
        ok = lambda t: self.violation(book.plus(cluster, shares, cost, wins, p, t)) <= allowed
        if ok(1.0):
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if ok(mid) else (lo, mid)
        return lo
