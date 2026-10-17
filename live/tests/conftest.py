import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

LIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIVE))

import settings  # noqa: E402


@pytest.fixture
def s():
    return replace(settings.load(), armed=True)


@pytest.fixture(autouse=True)
def kill_switch(tmp_path, monkeypatch):
    """Never let a test see, or write, the real live/STOP."""
    path = tmp_path / "STOP"
    import preflight
    import trader
    monkeypatch.setattr(preflight, "KILL_SWITCH", path)
    monkeypatch.setattr(trader, "KILL_SWITCH", path)
    return path


def fair_row(market_id="m1", ticker="AAPL", contract_type="hit_up", barrier=300.0,
             fair_K=0.30, fair_atm=0.30, **flags):
    row = {"market_id": market_id, "ticker": ticker, "contract_type": contract_type,
           "barrier": barrier, "fair_K": fair_K, "fair_atm": fair_atm,
           "deadline_utc": pd.Timestamp("2099-01-01", tz="UTC"), "period": "weekly",
           "spot": 290.0, "touch_pending": False, "deep_otm": False, "no_chain": False,
           "quote_age_s": 800.0, "earnings_gap": False, "vol_source": "strike"}
    row.update(flags)
    return row


def book_rows(market_id="m1", ticker="AAPL", contract_type="hit_up", outcome_index=0,
              asks=((0.20, 100.0),), token_id=None):
    return [{"market_id": market_id, "ticker": ticker, "contract_type": contract_type,
             "outcome_index": outcome_index, "side": "ask", "depth_rank": i,
             "price": p, "size": size, "token_id": token_id or f"{market_id}-{outcome_index}"}
            for i, (p, size) in enumerate(asks)]
