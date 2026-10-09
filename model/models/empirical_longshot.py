"""C — `empirical_longshot`: no implied vol, two shapes, one hard stop.

MODELS 5.3. The signal is the historical touch frequency of section 3.5, not
the option surface, and the option engine appears exactly once - as a veto.
If `fair_K` says a longshot is worth more than it is being asked for, this
model does not sell it; it never uses `fair_K` as a reason *to* trade.

Two shapes and no others:

* **Sell longshots.** Buy the NO when the YES ask is 12c or less, the
  empirical touch frequency is at most half the YES price, and the option
  engine does not disagree. ROI floor 2%, per-market cap $500, per-direction
  cap 30% - a crash touches every `hit_down` longshot at once and that is the
  risk this model is carrying on purpose.
* **Buy the middle.** Buy the YES when the ask is between 15c and 50c and the
  empirical frequency beats the all-in cost by at least 4c.

The hard rule is the interesting part: once SPY is down more than 1.5%
intraday, stop adding `hit_down` NO **for the rest of the day**, even if SPY
recovers. That is the one situation in which longshots stop being longshots,
and a rule that un-fires when the tape bounces is not a stop.

`deep_otm` is not excluded here, unlike in A and B. MODELS 3.4 forbids trading
those *on the option signal*; this model's signal is five years of realised
highs and lows, and the far barriers are precisely the ones the longshot shape
is about. The flag is carried into the ledger so the effect can be read off
afterwards.
"""
from __future__ import annotations

import numpy as np

import sim
from sim import Order, Params, YES, NO


class EmpiricalLongshot:
    name = "empirical_longshot"

    #: MODELS 5.3: per-direction 30%, per-market $500 on the NO side.
    params = Params(direction_cap_frac=0.30, cap_no_usd=500.0)

    #: MODELS 5.3's stop. Not a parameter with a shrug: -1.5% is the number.
    spy_stop = -0.015

    def __init__(self, longshot_max_price: float = 0.12, longshot_roi: float = 0.02,
                 middle_low: float = 0.15, middle_high: float = 0.50,
                 middle_min_ev: float = 0.04):
        self.longshot_max_price = longshot_max_price
        self.longshot_roi = longshot_roi
        self.middle_low = middle_low
        self.middle_high = middle_high
        self.middle_min_ev = middle_min_ev
        #: Dates on which the SPY rule has fired. It never un-fires within a day.
        self.stopped_days: set[str] = set()

    def stop_is_on(self, state: sim.CycleState) -> bool:
        date = sim.data.cycle_date(state.cycle_id)
        move = state.spy_intraday
        if np.isfinite(move) and move < self.spy_stop:
            self.stopped_days.add(date)
        return date in self.stopped_days

    def propose(self, state: sim.CycleState) -> list[Order]:
        fair = state.fair
        if fair.empty:
            return []
        stopped = self.stop_is_on(state)
        universe = fair[~fair["touch_pending"].fillna(False).astype(bool)]
        universe = universe[universe["fair_empirical"].notna()]
        if universe.empty:
            return []

        asks = state.best_asks()
        yes = asks[asks["outcome_index"] == YES][["market_id", "ask"]] \
            .rename(columns={"ask": "yes_ask"})
        no = asks[asks["outcome_index"] == NO][["market_id", "ask"]] \
            .rename(columns={"ask": "no_ask"})
        joined = universe.merge(yes, on="market_id", how="left") \
                         .merge(no, on="market_id", how="left")
        fee = state.params.fee_rate
        params = state.params
        orders: list[Order] = []

        for row in joined.itertuples():
            emp = float(row.fair_empirical)
            yes_ask, no_ask = float(row.yes_ask), float(row.no_ask)

            # -- shape 1: sell the longshot -------------------------------
            if (np.isfinite(yes_ask) and np.isfinite(no_ask)
                    and 0.0 < yes_ask <= self.longshot_max_price
                    and emp <= 0.5 * yes_ask
                    and not self._vetoed(row, yes_ask)
                    and not (stopped and row.contract_type == "hit_down")):
                cost = no_ask + fee * min(no_ask, 1.0 - no_ask)
                win = 1.0 - emp
                if cost > 0 and (win - cost) / cost >= self.longshot_roi:
                    orders.append(Order(
                        market_id=row.market_id, outcome_index=NO,
                        max_usd=params.cap_no_usd,
                        limit_price=win / (1.0 + self.longshot_roi),
                        reason="sell_longshot", fair=emp, fair_source="empirical"))
                    continue

            # -- shape 2: buy the middle -----------------------------------
            if np.isfinite(yes_ask) and self.middle_low <= yes_ask <= self.middle_high:
                cost = yes_ask + fee * min(yes_ask, 1.0 - yes_ask)
                if emp - cost >= self.middle_min_ev:
                    # The shape is "the ask is between 15c and 50c", and the
                    # harness walks the ladder past the top level, so the limit
                    # must also stop the walk at the shape's ceiling. Found by
                    # the behaviour checks (2026-09-18): two fills at 51c and
                    # 52c behind a 50c top of book.
                    ceiling = self.middle_high + fee * min(self.middle_high, 1.0 - self.middle_high)
                    orders.append(Order(
                        market_id=row.market_id, outcome_index=YES,
                        max_usd=params.cap_yes_usd,
                        limit_price=min(emp - self.middle_min_ev, ceiling + 1e-9),
                        reason="buy_middle", fair=emp, fair_source="empirical"))
        return orders

    @staticmethod
    def _vetoed(row, yes_ask: float) -> bool:
        """MODELS 5.3's veto: the option engine may only say no.

        A missing `fair_K` is not a veto. Roughly a fifth of the universe is
        commodity and FX ladders with no equity chain at all (measured 2026-09-18), and
        treating "the engine has no opinion" as "the engine disagrees" would
        delete the part of the universe this model exists to trade.
        """
        fair_k = float(getattr(row, "fair_K", float("nan")))
        return bool(np.isfinite(fair_k) and fair_k > yes_ask)
