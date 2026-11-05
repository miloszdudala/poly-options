"""Step 10 - account: how much the model may spend. Bookkeeping, not a bet.

| variant | cash | decided / open |
|---|---|---|
| `max` | unlimited: every bet as large as the book allows; capital used = sum of bets | **decided**: the routine account |
| `compound` | starts at `start` ($100), reinvests what comes back | on demand |
| `budget` | `per_period` dollars each day / week from Monday / month (New York time) of the type; no roll-over, payouts do not refill it | on demand |
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

from pipeline.core import variant

EASTERN = "America/New_York"


def period_key(when, period: str) -> str:
    """The day, week (starting Monday) or month `when` falls in, New York time."""
    local = pd.Timestamp(when).tz_convert(EASTERN)
    if period == "daily":
        return local.date().isoformat()
    if period == "monthly":
        return f"{local.year:04d}-{local.month:02d}"
    return (local.date() - dt.timedelta(days=local.weekday())).isoformat()


class Account:
    name = ""

    def cash(self) -> float:
        raise NotImplementedError

    def start_cycle(self, now, period: str) -> None:
        pass

    def spend(self, usd: float) -> None:
        pass

    def receive(self, usd: float) -> None:
        pass


@variant("account", "max")
class MaxAccount(Account):
    name = "max"

    def cash(self):
        return math.inf


@variant("account", "compound")
class CompoundAccount(Account):
    name = "compound"

    def __init__(self, start: float = 100.0):
        self.balance = float(start)

    def cash(self):
        return self.balance

    def spend(self, usd):
        self.balance -= usd

    def receive(self, usd):
        self.balance += usd


@variant("account", "budget")
class BudgetAccount(Account):
    name = "budget"

    def __init__(self, per_period: float):
        self.per_period = float(per_period)
        self.left = float(per_period)
        self._period = None

    def cash(self):
        return self.left

    def start_cycle(self, now, period):
        key = period_key(now, period)
        if key != self._period:
            self._period, self.left = key, self.per_period

    def spend(self, usd):
        self.left -= usd
