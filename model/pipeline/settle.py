"""Step 11 - settle: what a resolved market pays. Bookkeeping, not a bet.

`result` is the venue's winning label; it is read against the market's own
outcome labels, so a Yes/No market and an Up/Down market settle alike:
outcome 0 won pays a YES 1, outcome 1 won pays a NO 1. A 50-50 settlement
(`result` null, `outcome_prices` 0.5/0.5) pays 0.5; a null result with any
other prices is a pending dispute and is not booked.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import store

YES, NO = "YES", "NO"


def resolutions(upto) -> pd.DataFrame:
    """Every market settled by `upto`, `result` as 'Yes' (outcome 0 won) or
    'No' (outcome 1 won)."""
    frame = store.resolutions_upto(upto)
    if frame.empty:
        return frame
    labels = store.outcome_labels()
    ids = frame["market_id"].astype(str)
    result = frame["result"].astype(object)
    if len(labels):
        first, second = ids.map(labels["outcome_0"]), ids.map(labels["outcome_1"])
        result = np.where(result == first, "Yes", np.where(result == second, "No", result))
    return frame.assign(market_id=ids, result=result)


def payout_per_share(result, outcome_prices, side: str) -> float | None:
    """1, 0 or 0.5 per share; None when the market has not really resolved."""
    if isinstance(result, str) and result in ("Yes", "No"):
        return 1.0 if (result == "Yes") == (side == YES) else 0.0
    prices = outcome_prices
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (ValueError, TypeError):
            return None
    if isinstance(prices, (list, tuple)) and len(prices) == 2:
        try:
            values = [float(x) for x in prices]
        except (TypeError, ValueError):
            return None
        if abs(values[0] - 0.5) < 1e-9 and abs(values[1] - 0.5) < 1e-9:
            return 0.5
    return None


def settle(open_positions: dict, resolved: pd.DataFrame, account) -> list:
    """Close every open position whose market has resolved."""
    if resolved is None or resolved.empty:
        return []
    by_market = (resolved.assign(market_id=resolved["market_id"].astype(str))
                 .drop_duplicates("market_id").set_index("market_id"))
    done = []
    for key in list(open_positions):
        market_id, side = key
        if market_id not in by_market.index:
            continue
        row = by_market.loc[market_id]
        payout = payout_per_share(row.get("result"), row.get("outcome_prices"), side)
        if payout is None:
            continue
        position = open_positions.pop(key)
        position.result = str(row.get("result"))
        position.settled_at = pd.Timestamp(row["settled_at_utc"])
        position.payout_usd = payout * position.shares
        account.receive(position.payout_usd)
        done.append(position)
    return done
