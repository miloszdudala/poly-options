"""The report builder's tables. Session arithmetic lives in test_sessions.py."""
import numpy as np
import pandas as pd
import pytest

import run_calibration as rc


# --------------------------------------------------------------- the tables


def _points(prices, outcomes, markets):
    return pd.DataFrame({
        "price": prices, "outcome": [float(o) for o in outcomes],
        "market_id": markets,
        "bucket": rc.cal.bucket_labels(np.asarray(prices, dtype=float)),
    })


def test_reliability_reports_the_gap_and_counts_both_points_and_markets():
    df = _points([0.04] * 4, [1, 0, 0, 0], ["a", "a", "b", "b"])
    row = rc.reliability(df, "yes").iloc[0]
    assert row["bucket"] == "3-5c"
    assert row["n"] == 4 and row["markets"] == 2
    assert row["mean_price"] == pytest.approx(0.04)
    assert row["realised"] == pytest.approx(0.25)
    assert row["gap"] == pytest.approx(0.21)


def test_reliability_mirrors_exactly_on_the_no_side():
    df = _points([0.04] * 4, [1, 0, 0, 0], ["a", "a", "b", "b"])
    yes = rc.reliability(df, "yes").iloc[0]
    no = rc.reliability(df, "no").iloc[0]
    assert no["mean_price"] == pytest.approx(1 - yes["mean_price"])
    assert no["realised"] == pytest.approx(1 - yes["realised"])
    assert no["gap"] == pytest.approx(-yes["gap"])


def test_reliability_clustered_interval_is_the_wider_one_when_markets_disagree():
    prices = [0.3] * 40
    outcomes = [1] * 20 + [0] * 20
    markets = ["a"] * 20 + ["b"] * 20      # each market is unanimous, they disagree
    row = rc.reliability(_points(prices, outcomes, markets), "yes").iloc[0]
    assert (row["chi"] - row["clo"]) > (row["bhi"] - row["blo"])


def test_reliability_skips_buckets_with_no_points():
    table = rc.reliability(_points([0.04, 0.04], [1, 0], ["a", "b"]), "yes")
    assert list(table["bucket"]) == ["3-5c"]


def test_fmt_reliability_renders_one_row_per_bucket():
    table = rc.reliability(_points([0.04, 0.9], [1, 0], ["a", "b"]), "yes")
    rendered = rc.fmt_reliability(table).splitlines()
    assert rendered[0].startswith("| bucket |")
    assert len(rendered) == 2 + len(table)


# ------------------------------------ review 2026-09-18: the hourly bar is a period


def test_hourly_spot_is_the_open_of_the_containing_bar_not_its_close():
    """yfinance stamps an hourly bar at its start. A 14:00Z point sits inside
    the 13:30Z bar, whose close is a 14:30Z print - the future."""
    hourly = pd.DataFrame({
        "symbol": ["X", "X"],
        "bar_time": pd.to_datetime(["2026-06-10T13:30:00Z", "2026-06-10T14:30:00Z"], utc=True),
        "open": [100.0, 110.0], "close": [110.0, 120.0],
    })
    times = pd.Series(pd.to_datetime(["2026-06-10T14:00:05Z", "2026-06-10T14:30:05Z"], utc=True))
    spot = rc.hourly_spot(pd.Series(["X", "X"]), times, hourly)
    assert list(spot) == [100.0, 110.0]
