"""End to end on one real cycle, and the alignment rules that make it right.

The cycle is 2026-09-16 15:51Z, the snapshot FINDINGS section 3 was written
from. PLTR was at 171.5 with a `hit_down 165` weekly barrier deadlining at the
Friday close, two and a bit sessions out. Any change that moves that market's
fair value outside [0.20, 0.45], or that lets the continuous formula fall below
the monitored one, has changed something the rest of the project believes.

These tests read the store, so they are integration tests and will skip rather
than fail if the cycle is not on disk.
"""
import numpy as np
import pandas as pd
import pytest

import data
import engine

CYCLE = "20260916T155117Z-5639a2"
PLTR_HIT_DOWN_165 = "4485785"


@pytest.fixture(scope="module")
def priced():
    if CYCLE not in data.session_cycles("2026-09-16"):
        pytest.skip(f"{CYCLE} is not in the store")
    frame, stats = engine.price_cycle(CYCLE)
    return frame, stats


def _market(frame, market_id):
    rows = frame[frame["market_id"] == market_id]
    if rows.empty:
        pytest.skip(f"market {market_id} not in this cycle")
    return rows.iloc[0]


def test_the_pltr_barrier_prices_where_findings_says_it_should(priced):
    frame, _ = priced
    row = _market(frame, PLTR_HIT_DOWN_165)
    assert row["contract_type"] == "hit_down" and row["barrier"] == 165.0
    assert 0.20 <= row["fair"] <= 0.45


def test_the_option_chain_is_about_fifteen_minutes_older_than_the_book(priced):
    """README: the delay is real, and `quote_age_s` is where it is admitted."""
    frame, _ = priced
    age = frame["quote_age_s"].dropna()
    assert 10 * 60 < np.median(age) < 20 * 60


def test_our_implied_vol_agrees_with_cboe_on_liquid_strikes(priced):
    """FINDINGS section 4: Cboe's IV is correct; ours must reproduce it."""
    _, stats = priced
    assert stats["iv_checked"] > 1000
    assert stats["iv_gap_median"] < 0.01


def test_every_market_is_keyed_once_per_cycle(priced):
    frame, _ = priced
    assert frame["market_id"].is_unique


def test_a_cycle_prices_in_well_under_a_minute(priced):
    _, stats = priced
    assert stats["seconds"] < 60


def test_markets_already_beyond_their_barrier_are_flagged_not_priced(priced):
    """MODELS 3.1: a pending touch is worth 1 by arithmetic, not by model."""
    frame, _ = priced
    pending = frame[frame["touch_pending"].astype(bool)]
    if pending.empty:
        pytest.skip("no touch-pending markets in this cycle")
    assert (pending["fair"] == 1.0).all()


def test_the_deadline_is_a_session_close_never_the_raw_end_date(priced):
    """A monthly `end_date` of 2026-10-01T03:59:59Z deadlines at 30 Sep's close.

    Asserted against the calendar rather than against "20:00Z": the December
    SPX ladders deadline at 21:00Z because the clocks go back on 1 November,
    and a half-day closes at 18:00Z.
    """
    from sessions import nyse_sessions

    frame, _ = priced
    closes = set(pd.to_datetime(nyse_sessions()["close"], utc=True))
    deadlines = pd.to_datetime(frame["deadline_utc"].dropna(), format="ISO8601", utc=True)
    assert len(deadlines) > 100
    assert set(deadlines) <= closes
    monthly = frame[frame["period"] == "monthly"].dropna(subset=["deadline_utc", "end_date"])
    if len(monthly):
        raw = pd.to_datetime(monthly["end_date"], format="ISO8601", utc=True)
        got = pd.to_datetime(monthly["deadline_utc"], format="ISO8601", utc=True)
        assert (got <= raw).all()
        assert (got < raw).any()


def test_a_weekly_barrier_uses_the_expiry_that_matches_its_deadline(priced):
    """The Friday expiry and the Friday barrier end at the same instant."""
    frame, _ = priced
    row = _market(frame, PLTR_HIT_DOWN_165)
    assert row["expiry"] == "2026-09-18"
    assert bool(row["exact_expiry"])


def test_the_engine_reports_the_vol_it_priced_on(priced):
    frame, _ = priced
    row = _market(frame, PLTR_HIT_DOWN_165)
    assert np.isfinite(row["sigma_atm"]) and row["n_strikes"] > 0


def test_pricing_is_reproducible(priced):
    """Same cycle, same numbers."""
    frame, _ = priced
    again, _ = engine.price_cycle(CYCLE)
    merged = frame[["market_id", "fair"]].merge(again[["market_id", "fair"]],
                                                on="market_id", suffixes=("_a", "_b"))
    both = merged.dropna()
    assert len(both) > 100
    assert np.allclose(both["fair_a"], both["fair_b"])


def test_no_priced_market_had_its_barrier_printed_earlier_in_the_period(priced):
    """Review 2026-09-18: a touch is a path. Every market the engine prices
    must be one whose barrier no finished regular-hours bar has reached since
    the market began being watched; the ones that have are `touch_pending`,
    worth 1."""
    frame, _ = priced
    live = frame[frame["fair"].notna() & ~frame["touch_pending"].astype(bool)]
    assert len(live) > 100
    for row in live.itertuples():
        start = data.watch_start_for(row.market_id, row.deadline_utc, row.period)
        if pd.isna(start):
            continue
        seen = data.touched_between(row.ticker, float(row.barrier),
                                    row.contract_type == "hit_up", start,
                                    pd.Timestamp(row.book_timestamp_utc))
        assert not seen, (row.market_id, row.ticker, row.barrier)


def test_an_earnings_report_at_the_deadline_bell_is_a_gap_not_inside(priced):
    """MU reports 2026-09-30 after the close; its monthly deadline is that
    close; the expiry after is 2 October. The jump is in the option and not in
    the barrier, so the market is flagged `earnings_gap` (and not traded)."""
    frame, _ = priced
    mu = frame[(frame["ticker"] == "MU") & (frame["period"] == "monthly")
               & frame["fair"].notna() & frame["expiry"].notna()]
    if mu.empty:
        pytest.skip("no priced MU monthly in this cycle")
    assert mu["earnings_gap"].all()
    assert not mu["earnings_inside"].any()
    assert not frame["earnings_inside"].fillna(False).astype(bool).any()
