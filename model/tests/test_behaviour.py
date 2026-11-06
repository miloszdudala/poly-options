"""Behaviour checks on the store: is the pipeline doing what it says, before
there are outcomes to judge it by?

    .venv/bin/python -m pytest model/tests/test_behaviour.py -v -rs

Every test here reads what the collector, the engine and the last model run
left on disk, across every priced date, and asserts an invariant that must
hold whatever the markets do. None of them looks at PnL. They are meant to be
run a few days into the collection, when there is enough replayed data to be
wrong about and no resolution sample yet - so that anything found is fixed
before the end-of-month evaluation, not discovered inside it.

Each docstring says what a failure means. The thresholds are at the top and
are deliberately loose: a failure here should be a defect, not noise.

The tests skip, not fail, when the store is empty. They do not re-run the
models: the ledger under `model/store/ledger` is whatever `run_all.py` or
`daily.py` last wrote, and that is what they check.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import lab
import store
from pipeline import config as configs
from reporting import ledger_schema, results

# --------------------------------------------------------------- thresholds

MIN_CYCLES_PER_FULL_DATE = 16     # 15-minute cadence over 6.5 h is ~26; 09-16 had 21
QUOTE_AGE_RANGE_S = (120, 1800)   # Cboe's 15-minute delay, with room either side
MIN_BAR_SPOT_SHARE = 0.95         # equity market-cycles whose spot came from a 5m bar
MAX_SPOT_VS_CBOE = 0.02           # bar spot against Cboe's delayed last price
OPEN_SETTLED_Z = "140000"         # 30 min after the (DST) open; see the spot test
MAX_FAIR_JUMP = 0.25              # between consecutive cycles, spot and T unchanged
MAX_FAIR_JUMP_SHARE = 0.01        # share of consecutive pairs allowed past it
MAX_REPRICE_DIFF = 1e-3           # store vs a fresh reprice of the same cycle
MAX_VENUE_DISAGREEMENT = 0.10     # touched-on-our-bars vs resolved-YES-at-the-venue
EQUITY_EXCLUDED = {"DXY", "WTI", "XAUUSD", "XAGUSD", "NG"}
# Dates known to be thin, with why. A new entry needs a reason, not a shrug.
KNOWN_THIN_DATES = {
    # Lid closed 21:06-00:15 local: 12 of 26 cycles (README, 2026-09-23).
    "2026-09-21",
    # Collector outages; every request failed (September review 2026-10-01 and
    # the 10-01 collector table: 22 of 23 cycles not ok).
    "2026-09-29",
    "2026-10-01",
    # 9 of 11 snapshots hit their deadline and skipped the option chains
    # ("deadline hit after kalshi/polymarket books: options skipped", runs
    # table): 1 cycle could be priced.
    "2026-10-12",
}

YES, NO = 0, 1


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def dates():
    try:
        found = store.dates()
    except Exception:              # no store here (a cloud session)
        found = []
    if not found:
        pytest.skip("no store")
    return found


@pytest.fixture(scope="module")
def fair(dates):
    """Every priced market-cycle of the MVP config (cached), with the old
    flag columns derived from the gate that stopped each market."""
    frame = lab.priced_window(configs.load("mvp"), dates)
    if frame.empty:
        pytest.skip("nothing priced")
    frame["date"] = frame["cycle_id"].map(store.cycle_date)
    frame["deadline_utc"], frame["barrier"] = frame["deadline"], frame["level"]
    for flag in ("touch_pending", "deep_otm", "earnings_gap"):
        frame[flag] = frame["gate"] == flag
    frame["no_chain"] = frame["gate"].fillna("").str.startswith("no vol")
    return frame


def _ledger(table: str) -> pd.DataFrame:
    root = ledger_schema.STORE / "ledger"
    parts = list(root.glob(f"*/{table}/*.parquet"))
    if not parts:
        return pd.DataFrame()
    # `model` becomes the run's own name (model, type, account, tag), so a
    # group-by per model is a group-by per replay.
    frame = pd.concat([pd.read_parquet(p).assign(model=p.parent.parent.name) for p in parts],
                      ignore_index=True)
    for col in ("filled_at_utc", "entry_at_utc", "settled_at_utc", "deadline_utc"):
        if col in frame:
            frame[col] = pd.to_datetime(frame[col], format="ISO8601", utc=True, errors="coerce")
    return frame


@pytest.fixture(scope="module")
def fills():
    frame = _ledger("fills")
    if frame.empty:
        pytest.skip("no ledger: run model/run.py --save or model/run.py daily first")
    # These checks read Polymarket's hit books and pricing, which only the hit
    # configs use. The chain and Kalshi extensions have their own tests.
    return frame[frame["contract_type"].isin(["hit_up", "hit_down"])].reset_index(drop=True)


@pytest.fixture(scope="module")
def settlements():
    bets = _ledger("bets")
    return bets[bets["result"].notna()] if len(bets) else bets


@pytest.fixture(scope="module")
def venue():
    """Every resolution the collector has, latest row per market."""
    try:
        frame = store.resolutions_upto("2100-01-01")
    except Exception:              # no store here (a cloud session)
        pytest.skip("no store")
    return frame.set_index("market_id") if len(frame) else frame


@pytest.fixture(scope="module")
def asks(fills):
    """Every ask level of every cycle any model filled in, one query."""
    cycles = sorted(set(fills["cycle_id"]))
    quoted = ", ".join(f"'{c}'" for c in cycles)
    frame = store._q(f"""
        select cycle_id, market_id, outcome_index, depth_rank, price, size
        from {store._src('poly_books')}
        where side = 'ask' and cycle_id in ({quoted})
    """)
    return frame


def _fair_at(fair: pd.DataFrame) -> pd.DataFrame:
    return fair.set_index(["cycle_id", "market_id"]).sort_index()


# =========================================================== 1. the collector


def test_every_in_session_cycle_with_books_is_priced(dates, fair):
    """A cycle with books and no fair values is a cycle no model could trade.
    Failure: `daily.py` step 1 did not run, or `price_missing` failed on it."""
    book_dates = store._q(f"select distinct date from {store._src('poly_books')} order by 1")
    # Today's books are priced by the evening `daily.py`; until it runs they
    # are not a failure (2026-10-19 failed all day for that reason).
    today = pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d")
    missing = {}
    for date in book_dates["date"].astype(str):
        if date in store.EXCLUDED_DATES or date >= today:
            continue
        cycles = store.session_cycles(date)
        if not cycles:
            continue
        priced = set(fair.loc[fair["date"] == date, "cycle_id"])
        gap = sorted(set(cycles) - priced)
        if gap:
            missing[date] = gap
    assert not missing, f"session cycles with books but no fair values: {missing}"


def test_each_completed_date_has_a_full_day_of_cycles(dates, fair):
    """Fewer cycles than a session holds means the collector slept or crashed,
    and that date's replay is a thin market, not a quiet one. The newest date
    is exempt because it may still be running."""
    counts = fair.groupby("date")["cycle_id"].nunique()
    completed = counts.drop(index=[dates[-1], *KNOWN_THIN_DATES], errors="ignore")
    thin = completed[completed < MIN_CYCLES_PER_FULL_DATE]
    assert thin.empty, f"dates with too few priced cycles: {thin.to_dict()}"


def test_option_quotes_are_the_expected_age_in_every_cycle(fair, fills):
    """`quote_age_s` should sit near Cboe's 15-minute delay. Far above means the
    chain is stale or missing for the cycle. Negative means the chain was
    fetched so long after the books that its prints are newer than the book
    (a slow tick, 2026-09-16 13:40Z): the harness must not let an option
    model fill on such a cycle, and there should be at most one per date."""
    equities = fair[~fair["ticker"].isin(EQUITY_EXCLUDED) & fair["quote_age_s"].notna()]
    by_cycle = equities.groupby("cycle_id")["quote_age_s"].median()
    # A stale chain is expected for the first ~15-30 minutes of a session
    # (Cboe's delay: its latest prints are yesterday's). What must not happen
    # is an option fair built from one - engine.STALE_CHAIN_S.
    priced = equities[equities["fair"].notna() & ~equities["touch_pending"]]
    stale = priced.groupby("cycle_id")["quote_age_s"].median()
    stale = stale[stale > QUOTE_AGE_RANGE_S[1]]
    assert stale.empty, f"option fairs built from a chain older than {QUOTE_AGE_RANGE_S[1]}s: {stale.round().to_dict()}"
    reversed_ = by_cycle[by_cycle < 0]
    per_date = reversed_.groupby(reversed_.index.map(store.cycle_date)).size()
    assert (per_date <= 1).all(), f"more than one chain-after-book cycle on a date: {per_date.to_dict()}"
    option = fills[fills["model"].str.startswith("edge_taker")]
    ages = fair.set_index(["cycle_id", "market_id"])["quote_age_s"]
    per_fill = [ages.get((c, m), np.nan) for c, m in zip(option["cycle_id"], option["market_id"])]
    contaminated = option[np.asarray(per_fill, dtype=float) < 0]
    assert contaminated.empty, (
        f"{len(contaminated)} option-signal fills priced off a chain newer than the book: "
        f"{contaminated[['model', 'ticker', 'cycle_id']].head().to_dict('records')}")


def test_spot_comes_from_a_bar_for_nearly_every_equity_market(fair):
    """A `cboe_delayed` spot is 15 minutes stale. A rising share of them means
    the 5-minute bars stopped arriving (the daily job) or a symbol changed."""
    equities = fair[~fair["ticker"].isin(EQUITY_EXCLUDED)]
    share = (equities["spot_source"] == "bar_5m").mean()
    assert share >= MIN_BAR_SPOT_SHARE, f"only {share:.1%} of equity market-cycles have a bar spot"


def test_bar_spot_agrees_with_cboe_s_delayed_price(fair, dates):
    """Cboe's delayed last against the high-low range of our 5-minute bar at
    Cboe's own quote time: the same stock at the same moment. Outside that
    range by more than 2% is a symbol mismatch or a bar stamped on the wrong
    clock. The range, not the bar's open: OPEN fell 2.115 -> 2.060 inside the
    13:40Z bar of 10-19 and Cboe's 13:44 last (2.065) is inside it.

    Compared at Cboe's quote time, not the cycle's, since 2026-10-20. Against
    the cycle's spot the test measured the stock's move over Cboe's delay,
    and failed on markets, not on data: COIN, MSTR and RKLB move 2-3% between
    9:45 and 10:00 ET, and on 09-23/24 Cboe's whole snapshot was the previous
    close (quote times 09-22 19:59Z, 72,000 s old; GOOGL 351.80 against bars
    at 340.7, which were right).

    The first 30 minutes of a session are still left out: Cboe's delayed last
    is then yesterday's close or the opening print."""
    worst = {}
    bars = store.bars_5m()
    for date in dates:
        cycles = sorted(c for c in set(fair.loc[fair["date"] == date, "cycle_id"])
                        if c[9:15] >= OPEN_SETTLED_Z)
        for cycle_id in cycles[:: max(1, len(cycles) // 3)]:      # a few per date
            cboe = store.cboe_spot(cycle_id).set_index("symbol")["last_price"]
            rows = fair[(fair["cycle_id"] == cycle_id) & (fair["spot_source"] == "bar_5m")
                        & fair["quote_time_utc"].notna()]
            rows = rows.drop_duplicates("ticker")
            if rows.empty:
                continue
            at = pd.to_datetime(rows["quote_time_utc"], utc=True, format="ISO8601")
            for ticker, when in zip(rows["ticker"], at):
                ref = cboe.get(ticker)
                bar = bars[(bars["symbol"] == ticker) & (bars["bar_time"] <= when)].tail(1)
                if not (ref and np.isfinite(ref) and ref > 0) or bar.empty:
                    continue
                lo, hi = float(bar["low"].iloc[0]), float(bar["high"].iloc[0])
                gap = max(lo - ref, ref - hi, 0.0) / ref
                if gap > MAX_SPOT_VS_CBOE:
                    worst[(cycle_id, ticker)] = round(gap, 4)
    assert not worst, f"bar spot vs Cboe last past {MAX_SPOT_VS_CBOE:.0%}: {worst}"


# ============================================================== 2. the engine


def test_every_fair_value_is_a_probability(fair):
    vals = fair["fair"].dropna()
    assert ((vals >= 0) & (vals <= 1)).all(), "fair outside [0, 1]"


def test_a_market_s_deadline_never_moves_and_its_horizon_only_shrinks(fair):
    """The deadline is a property of the market; `T_sessions` must fall from
    cycle to cycle. Either moving means the session calendar or the clock
    changed under a running market."""
    per_market = fair.groupby("market_id")["deadline_utc"].nunique()
    assert (per_market <= 1).all(), f"markets whose deadline changed: {per_market[per_market > 1].index.tolist()[:10]}"
    ordered = fair.sort_values(["market_id", "cycle_id"])
    ordered["prev_T"] = ordered.groupby("market_id")["T_sessions"].shift()
    grew = ordered[(ordered["T_sessions"] > ordered["prev_T"] + 1e-9)]
    assert grew.empty, f"{len(grew)} market-cycles where T_sessions grew"


def test_touch_pending_markets_carry_a_fair_of_one_and_no_option_signal_uses_them(fair, fills):
    pending = fair[fair["touch_pending"]]
    if pending.empty:
        pytest.skip("no touch-pending market-cycles yet")
    assert (pending["fair"] == 1.0).all()
    keyed = set(zip(pending["cycle_id"], pending["market_id"]))
    traded = fills[[(c, m) in keyed for c, m in zip(fills["cycle_id"], fills["market_id"])]]
    assert traded.empty, f"{len(traded)} fills on touch-pending markets: {traded[['model', 'market_id', 'cycle_id']].head().to_dict('records')}"


def test_what_our_bars_call_a_touch_the_venue_resolves_yes(fair, venue):
    """The engine's path check (finished regular-hours bars from the period
    start) against Pyth's 1-minute candles. A market we flagged as touched
    that the venue then resolved NO means our bars printed something Pyth did
    not - a feed, a session-window or a stamp problem, and the models are
    skipping markets they could have traded."""
    if venue.empty:
        pytest.skip("no resolutions yet")
    pending = fair[fair["touch_pending"]].drop_duplicates("market_id")
    resolved = pending[pending["market_id"].isin(venue.index)]
    if resolved.empty:
        pytest.skip("no touch-pending market has resolved yet")
    results = venue.loc[resolved["market_id"], "result"]
    wrong = resolved[(results != "Yes").values]
    rate = len(wrong) / len(resolved)
    assert rate <= MAX_VENUE_DISAGREEMENT, (
        f"{len(wrong)} of {len(resolved)} touch-pending markets resolved NO: "
        f"{wrong[['ticker', 'contract_type', 'barrier', 'cycle_id']].head().to_dict('records')}")


def test_what_the_venue_resolves_yes_our_bars_also_saw(fair, venue):
    """The mirror: every market the venue settled YES inside the window should
    show a print on our bars by its settlement time. Misses are Pyth prints
    between our bar highs and lows - some are expected, many mean our bars
    are late, thin or on the wrong session window."""
    if venue.empty:
        pytest.skip("no resolutions yet")
    first_cycle = pd.Timestamp(store.cycle_start(sorted(fair["cycle_id"])[0]))
    yes = venue[(venue["result"] == "Yes") & (venue["settled_at_utc"] >= first_cycle)]
    yes = yes[yes.index.isin(set(fair["market_id"]))]
    if yes.empty:
        pytest.skip("no YES resolution inside the replay window yet")
    meta = fair.drop_duplicates("market_id").set_index("market_id")
    missed = []
    for market_id, row in yes.iterrows():
        m = meta.loc[market_id]
        start = store.watch_start_for(market_id, m["deadline_utc"], m["period"])
        if pd.isna(start):
            continue
        seen = store.touched_between(m["ticker"], float(m["barrier"]),
                                    m["contract_type"] == "hit_up", start,
                                    row["settled_at_utc"] + pd.Timedelta(hours=1))
        if seen is False:
            missed.append((market_id, m["ticker"], m["contract_type"], float(m["barrier"])))
    rate = len(missed) / len(yes)
    assert rate <= MAX_VENUE_DISAGREEMENT, (
        f"{len(missed)} of {len(yes)} venue YES resolutions have no print on our bars: {missed[:5]}")


def test_fair_values_do_not_jump_between_cycles_without_a_reason(fair):
    """Same market, consecutive cycles, spot within 0.5% and the same session:
    `fair` should not move by more than 0.25. Jumps mean the smile is being
    built from a different set of contracts cycle to cycle (a filter on the
    edge) or the expiry pairing flipped."""
    ordered = fair[fair["fair"].notna() & ~fair["touch_pending"]].sort_values(["market_id", "cycle_id"])
    g = ordered.groupby("market_id")
    ordered["prev_fair"] = g["fair"].shift()
    ordered["prev_spot"] = g["spot"].shift()
    ordered["prev_T"] = g["T_sessions"].shift()
    same = ordered[ordered["prev_fair"].notna()
                   & ((ordered["spot"] / ordered["prev_spot"] - 1).abs() < 0.005)
                   & (np.floor(ordered["T_sessions"]) == np.floor(ordered["prev_T"]))]
    if same.empty:
        pytest.skip("no consecutive cycle pairs yet")
    jumped = same[(same["fair"] - same["prev_fair"]).abs() > MAX_FAIR_JUMP]
    share = len(jumped) / len(same)
    assert share <= MAX_FAIR_JUMP_SHARE, (
        f"{len(jumped)} of {len(same)} consecutive pairs jumped more than {MAX_FAIR_JUMP}: "
        f"{jumped[['ticker', 'barrier', 'cycle_id', 'prev_fair', 'fair']].head().to_dict('records')}")


def test_the_cache_matches_a_fresh_reprice(fair, dates):
    """Reprice the newest cycle without the cache and compare: the cache key
    covers settings and code, so a difference means it missed something."""
    newest = sorted(set(fair.loc[fair["date"] == dates[-1], "cycle_id"]))[-1]
    frame = lab.inspect(configs.load("mvp"), newest)
    stored = fair[fair["cycle_id"] == newest][["market_id", "fair", "gate"]]
    merged = stored.merge(frame[["market_id", "fair", "gate"]], on="market_id",
                          suffixes=("_store", "_now"))
    both = merged[merged["fair_store"].notna() & merged["fair_now"].notna()]
    diff = (both["fair_store"] - both["fair_now"]).abs().max()
    assert diff < MAX_REPRICE_DIFF, f"cache vs fresh reprice of {newest}: max |dfair| = {diff:.4g}"


def test_an_earnings_gap_is_never_traded_on_the_option_signal(fair, fills):
    gap = fair[fair["earnings_gap"] & fair["fair"].notna() & ~fair["touch_pending"]]
    if gap.empty:
        pytest.skip("no earnings-gap market-cycles yet")
    keyed = set(zip(gap["cycle_id"], gap["market_id"]))
    option_models = fills[fills["model"].str.startswith("edge_taker")]
    traded = option_models[[(c, m) in keyed for c, m in
                            zip(option_models["cycle_id"], option_models["market_id"])]]
    assert traded.empty, f"{len(traded)} option-signal fills on an earnings gap"


# ============================================================== 3. the ledger


def test_every_fill_took_a_level_that_was_in_the_book(fills, asks):
    """Price must equal an ask level of the right token in that cycle, and the
    shares at most `take_fraction` of that level. Anything else is a fill the
    harness invented."""
    keyed = asks.set_index(["cycle_id", "market_id", "outcome_index"]).sort_index()
    bad = []
    for f in fills.itertuples():
        try:
            levels = keyed.loc[(f.cycle_id, f.market_id, int(f.outcome_index))]
        except KeyError:
            bad.append((f.model, f.market_id, f.cycle_id, "no book"))
            continue
        if isinstance(levels, pd.Series):
            levels = levels.to_frame().T
        match = levels[(levels["price"] - f.price).abs() < 1e-9]
        if match.empty:
            bad.append((f.model, f.market_id, f.cycle_id, f"price {f.price} not a level"))
        elif f.shares > 0.5 * float(match["size"].max()) + 1e-6:
            bad.append((f.model, f.market_id, f.cycle_id, "took more than half the level"))
    assert not bad, f"{len(bad)} fills not backed by the book: {bad[:5]}"


def test_fees_and_costs_are_on_the_price_paid(fills):
    # 4% of p(1-p) or nothing: the market's own schedule at the time.
    full = 0.04 * fills["price"] * (1 - fills["price"]) * fills["shares"]
    assert (np.isclose(fills["fee_usd"], full, atol=1e-6) | np.isclose(fills["fee_usd"], 0.0)).all()
    assert np.allclose(fills["cost_usd"], fills["price"] * fills["shares"] + fills["fee_usd"],
                       atol=1e-6)
    # The venue minimum is per order, and one order is a walk over several
    # levels: three $0.50 levels are a valid $1.50 order. Sum the walk; the
    # per-level check this replaced failed on exactly that (2026-09-21 14:06Z).
    walk = fills.groupby(["model", "market_id", "outcome_index", "cycle_id"])["cost_usd"].sum()
    assert (walk >= 1.0 - 1e-9).all(), \
        f"walks below the $1 venue minimum: {walk[walk < 1.0 - 1e-9].head().to_dict()}"


def test_no_fill_on_a_market_whose_barrier_had_already_printed(fills):
    """The review's finding 1, as a standing check on every new day of fills."""
    late = []
    for f in fills.itertuples():
        start = store.watch_start_for(f.market_id, f.deadline_utc, f.period)
        if pd.isna(start):
            continue
        if f.contract_type not in ("hit_up", "hit_down"):
            continue
        if store.touched_between(f.ticker, float(f.level), f.contract_type == "hit_up",
                                start, f.filled_at_utc) is True:
            late.append((f.model, f.ticker, f.contract_type, float(f.level), str(f.filled_at_utc)))
    assert not late, f"{len(late)} fills after the barrier had printed: {late[:5]}"


def test_one_position_per_market_per_direction_per_model(fills):
    """MODELS 4.2: no averaging down, never both sides of one market."""
    sides = fills.groupby(["model", "market_id"])["outcome_index"].nunique()
    assert (sides <= 1).all(), f"both sides held: {sides[sides > 1].index.tolist()[:5]}"
    cycles = fills.groupby(["model", "market_id", "outcome_index"])["cycle_id"].nunique()
    assert (cycles <= 1).all(), f"re-entered without the flag: {cycles[cycles > 1].index.tolist()[:5]}"


def test_settlements_agree_with_the_venue_and_with_arithmetic(settlements, venue):
    if settlements.empty:
        pytest.skip("nothing settled yet")
    for s in settlements.itertuples():
        row = venue.loc[s.market_id]
        assert str(row["result"]) == str(s.result), (s.model, s.market_id)
        won = (s.result == "Yes") if s.outcome_index == YES else (s.result == "No")
        assert abs(s.payout_usd - (s.shares if won else 0.0)) < 1e-6, (s.model, s.market_id)
        assert abs(s.pnl_usd - (s.payout_usd - s.cost_usd)) < 1e-6
        assert s.settled_at_utc >= s.entry_at_utc, (s.model, s.market_id)


# ========================================================= 4. model behaviour


def _fair_row(keyed, cycle_id, market_id):
    try:
        return keyed.loc[(cycle_id, market_id)]
    except KeyError:
        return None


def test_option_models_only_took_edges_that_cleared_the_bar(fills, fair):
    """edge_taker: the recorded win probability is the engine's fair for the
    side bought, the market was neither deep OTM nor chainless, and the price
    paid cleared today's EV and ROI floors against it."""
    keyed = _fair_at(fair)
    option = fills[fills["model"].str.startswith("edge_taker")]
    if option.empty:
        pytest.skip("no option-signal fills yet")
    bad = []
    for f in option.itertuples():
        row = _fair_row(keyed, f.cycle_id, f.market_id)
        if row is None:
            bad.append((f.model, f.market_id, "no fair row")); continue
        fair_yes = float(row["fair"])
        win = fair_yes if f.outcome_index == YES else 1.0 - fair_yes
        if not np.isfinite(win) or abs(win - f.win_prob) > 1e-9:
            bad.append((f.model, f.market_id, f"win_prob {f.win_prob} != {win}")); continue
        if bool(row["deep_otm"]) or bool(row["no_chain"]):
            bad.append((f.model, f.market_id, "deep_otm or no_chain")); continue
        cost = f.price + f.fee_usd / f.shares
        dear = f.price > 0.90
        ev_bar, roi_bar = (0.0, 0.02) if dear else (0.03, 0.08)
        if win - cost < ev_bar - 1e-9 or (win - cost) / cost < roi_bar - 1e-9:
            bad.append((f.model, f.market_id, f"win {win:.3f} cost {cost:.3f}"))
    assert not bad, f"{len(bad)} option-signal fills that did not clear the bar: {bad[:5]}"


def test_the_daily_report_exists_for_the_newest_priced_date_and_warns(dates):
    path = results.REPORTS / f"daily_{dates[-1]}.md"
    assert path.exists(), f"no daily report for {dates[-1]}: run model/run.py daily"
    text = path.read_text()
    assert len(text.splitlines()) <= 120
    assert "not" in text.lower() and "result" in text.lower(), "the selection warning is missing"
