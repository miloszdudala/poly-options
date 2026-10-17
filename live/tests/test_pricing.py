import pandas as pd
import pytest
from conftest import LIVE

import pricing

def bars(ticker="AAPL", closes=(100.0, 101.0), highs=None, lows=None,
         start="2026-10-20 14:00"):
    times = pd.date_range(start, periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame({"symbol": ticker, "bar_time": times, "open": closes,
                         "high": highs or closes, "low": lows or closes, "close": closes})


def fair(spot=100.0, delta=0.02, fair_K=0.30, fair_atm=0.32, ticker="AAPL"):
    return pd.DataFrame([{"market_id": "m1", "ticker": ticker, "spot": spot, "delta": delta,
                          "fair_K": fair_K, "fair_atm": fair_atm}])


def test_fair_moves_along_its_delta_to_the_latest_close():
    out = pricing.move_fair(fair(), bars(closes=(100.0, 100.5)), max_move=0.01)
    assert out["fair_K"].iloc[0] == pytest.approx(0.30 + 0.02 * 0.5)
    assert out["fair_atm"].iloc[0] == pytest.approx(0.32 + 0.02 * 0.5)
    assert out["spot"].iloc[0] == 100.5


def test_a_big_move_drops_the_market():
    assert pricing.move_fair(fair(), bars(closes=(100.0, 101.5)), max_move=0.01).empty


def test_no_current_spot_drops_the_market():
    assert pricing.move_fair(fair(ticker="MSFT"), bars(), max_move=0.01).empty


def test_moved_fair_stays_a_probability():
    out = pricing.move_fair(fair(fair_K=0.99, fair_atm=0.99, delta=1.0),
                            bars(closes=(100.0, 100.9)), max_move=0.01)
    assert out["fair_K"].iloc[0] == 1.0


def test_the_forming_bar_counts_as_a_print():
    b = bars(closes=(100.0, 100.0), highs=[100.0, 105.0])
    since = pd.Timestamp("2026-10-20 14:03", tz="UTC")
    assert pricing.printed_since(b, "AAPL", 104.0, True, since)
    assert not pricing.printed_since(b, "AAPL", 106.0, True, since)
    assert not pricing.printed_since(b, "AAPL", 99.0, False, since)


def test_a_print_before_the_snapshot_is_the_engine_s_business():
    b = bars(closes=(100.0, 100.0), highs=[105.0, 100.0])
    since = pd.Timestamp("2026-10-20 14:06", tz="UTC")
    assert not pricing.printed_since(b, "AAPL", 104.0, True, since)


@pytest.mark.parametrize("name", ["engine", "fair", "sessions", "empirical", "data"])
def test_the_engine_copy_matches_model(name):
    """pricing_engine/README.md: a change on either side must be a decision.

    Reads model/'s source as text and imports nothing from it. Skipped when
    model/ is absent, so live/ still runs on its own.
    """
    source_path = LIVE.parent / "model" / f"{name}.py"
    if not source_path.exists():
        pytest.skip("model/ is not here")
    source = source_path.read_text()
    copy = (LIVE / "pricing_engine" / f"{name}.py").read_text()
    if name == "data":
        copy = "\n".join(l for l in copy.splitlines()
                         if not l.startswith("# live/pricing_engine/")
                         and not l.startswith("# has one level")
                         and l != "REPO = HERE.parent.parent") + "\n"
        copy = copy.replace('REPO / "data"', 'HERE.parent / "data"')
    assert copy == source, (f"{name}.py differs between model/ and live/pricing_engine/: "
                            "copy it across, or keep live as it is and skip this on purpose")
