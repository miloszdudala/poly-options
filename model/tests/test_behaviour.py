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

import data
import engine
import ledger_schema
import sim

# --------------------------------------------------------------- thresholds

MIN_CYCLES_PER_FULL_DATE = 16     # 15-minute cadence over 6.5 h is ~26; 09-16 had 21
QUOTE_AGE_RANGE_S = (120, 1800)   # Cboe's 15-minute delay, with room either side
MIN_BAR_SPOT_SHARE = 0.95         # equity market-cycles whose spot came from a 5m bar
MAX_SPOT_VS_CBOE = 0.02           # bar spot against Cboe's delayed last price
OPEN_SETTLED_Z = "140000"         # 30 min after the (DST) open; see the spot test
MAX_FAIR_JUMP = 0.25              # between consecutive cycles, spot and T unchanged
MAX_FAIR_JUMP_SHARE = 0.01        # share of consecutive pairs allowed past it
MAX_REPRICE_DIFF = 1e-3           # store vs a fresh reprice of the same cycle
MAX_IV_DISAGREEMENT = 0.25        # share of liquid strikes past 0.01 vs Cboe
MAX_VENUE_DISAGREEMENT = 0.10     # touched-on-our-bars vs resolved-YES-at-the-venue
BAND_TOLERANCE = 1.10             # factor_neutral's entry band, with 10% slack
EQUITY_EXCLUDED = {"DXY", "WTI", "XAUUSD", "XAGUSD", "NG"}
# Dates known to be thin, with why. A new entry needs a reason, not a shrug.
KNOWN_THIN_DATES = {
    # Lid closed 21:06-00:15 local: 12 of 26 cycles (README, 2026-09-23).
    "2026-09-21",
    # Collector outages; every request failed (September review 2026-10-01 and
    # the 10-01 collector table: 22 of 23 cycles not ok).
    "2026-09-29",
    "2026-10-01",
}

YES, NO = 0, 1
CAPS_BY_MODEL = {
    # (cap_yes, cap_no, ticker_cap_frac, direction_cap_frac) - MODELS 4.4 and 5
    "edge_taker": (150.0, 500.0, 1.00, 1.00),
    "factor_neutral": (150.0, 500.0, 0.08, 0.25),
    "balanced_book": (150.0, 500.0, 0.08, 1.00),
    "direction_balanced": (150.0, 500.0, 0.10, 1.00),
    "scenario_hedged": (150.0, 500.0, 0.12, 1.00),
    "empirical_longshot": (150.0, 500.0, 0.10, 0.30),
    "random_control": (150.0, 500.0, 0.10, 0.40),
}
BANKROLL = 10_000.0


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def dates():
    priced = data.fair_dates()
    if not priced:
        pytest.skip("nothing priced: run model/run_fair.py or model/daily.py first")
    return priced


@pytest.fixture(scope="module")
def fair(dates):
    frame = pd.concat([data.fair_values(d) for d in dates], ignore_index=True)
    frame["date"] = frame["cycle_id"].map(data.cycle_date)
    for col in ("deep_otm", "no_chain", "touch_pending", "earnings_gap", "stale_book"):
        if col in frame:
            frame[col] = frame[col].fillna(False).astype(bool)
    return frame


def _ledger(table: str) -> pd.DataFrame:
    root = ledger_schema.STORE / "ledger"
    parts = list(root.glob(f"*/{table}/*.parquet"))
    if not parts:
        return pd.DataFrame()
    frame = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    for col in ("filled_at_utc", "marked_at_utc", "entry_at_utc", "settled_at_utc",
                "cash_back_at_utc", "deadline_utc"):
        if col in frame:
            frame[col] = pd.to_datetime(frame[col], format="ISO8601", utc=True, errors="coerce")
    return frame


@pytest.fixture(scope="module")
def fills():
    frame = _ledger("fills")
    if frame.empty:
        pytest.skip("no ledger: run model/run_all.py or model/daily.py first")
    return frame


@pytest.fixture(scope="module")
def marks():
    return _ledger("marks")


@pytest.fixture(scope="module")
def settlements():
    return _ledger("resolutions")


@pytest.fixture(scope="module")
def venue():
    """Every resolution the collector has, latest row per market."""
    frame = data.resolutions_upto("2100-01-01")
    return frame.set_index("market_id") if len(frame) else frame


@pytest.fixture(scope="module")
def asks(fills):
    """Every ask level of every cycle any model filled in, one query."""
    cycles = sorted(set(fills["cycle_id"]))
    quoted = ", ".join(f"'{c}'" for c in cycles)
    frame = data._q(f"""
        select cycle_id, market_id, outcome_index, depth_rank, price, size
        from {data._src('poly_books')}
        where side = 'ask' and cycle_id in ({quoted})
    """)
    return frame


def _fair_at(fair: pd.DataFrame) -> pd.DataFrame:
    return fair.set_index(["cycle_id", "market_id"]).sort_index()


# =========================================================== 1. the collector


def test_every_in_session_cycle_with_books_is_priced(dates):
    """A cycle with books and no fair values is a cycle no model could trade.
    Failure: `daily.py` step 1 did not run, or `price_missing` failed on it."""
    book_dates = data._q(f"select distinct date from {data._src('poly_books')} order by 1")
    missing = {}
    for date in book_dates["date"].astype(str):
        if date in data.EXCLUDED_DATES:
            continue
        cycles = data.session_cycles(date)
        if not cycles:
            continue
        priced = set(data.fair_values(date)["cycle_id"]) if date in dates else set()
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
    priced = equities[(equities["fair_K"].notna() | equities["fair_atm"].notna())
                      & ~equities["touch_pending"]]
    stale = priced.groupby("cycle_id")["quote_age_s"].median()
    stale = stale[stale > QUOTE_AGE_RANGE_S[1]]
    assert stale.empty, f"option fairs built from a chain older than {QUOTE_AGE_RANGE_S[1]}s: {stale.round().to_dict()}"
    reversed_ = by_cycle[by_cycle < 0]
    per_date = reversed_.groupby(reversed_.index.map(data.cycle_date)).size()
    assert (per_date <= 1).all(), f"more than one chain-after-book cycle on a date: {per_date.to_dict()}"
    option = fills[fills["model"].str.startswith(("edge_taker", "factor_neutral", "balanced_book", "direction_balanced", "scenario_hedged"))]
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
    """The two spots are the same stock 15 minutes apart. A gap past 2% on a
    median cycle is a symbol mismatch or a bar stamped on the wrong clock.

    The first 30 minutes of a session are left out: Cboe's delayed last is
    then yesterday's close or the opening print, and the open's quarter-hour
    moves more than 2% on its own (HOOD 2.6%, 2026-09-21 13:45Z)."""
    worst = {}
    for date in dates:
        cycles = sorted(c for c in set(fair.loc[fair["date"] == date, "cycle_id"])
                        if c[9:15] >= OPEN_SETTLED_Z)
        for cycle_id in cycles[:: max(1, len(cycles) // 3)]:      # a few per date
            cboe = data.cboe_spot(cycle_id).set_index("symbol")["last_price"]
            rows = fair[(fair["cycle_id"] == cycle_id) & (fair["spot_source"] == "bar_5m")]
            rows = rows.drop_duplicates("ticker")
            for row in rows.itertuples():
                ref = cboe.get(row.ticker)
                if ref and np.isfinite(ref) and ref > 0:
                    gap = abs(row.spot / ref - 1.0)
                    if gap > MAX_SPOT_VS_CBOE:
                        worst[(cycle_id, row.ticker)] = round(gap, 4)
    assert not worst, f"bar spot vs Cboe last past {MAX_SPOT_VS_CBOE:.0%}: {worst}"


# ============================================================== 2. the engine


def test_every_fair_value_is_a_probability_and_the_bound_holds(fair):
    for col in ("fair_K", "fair_atm", "fair_cont", "fair_empirical"):
        vals = fair[col].dropna()
        assert ((vals >= 0) & (vals <= 1)).all(), f"{col} outside [0, 1]"
    both = fair[fair["fair_K"].notna() & fair["fair_cont"].notna() & ~fair["touch_pending"]]
    # fair_K is a 20k-path Monte Carlo; three standard errors, not a flat 0.2c,
    # or a long-dated 14% barrier fails on noise (SPX 6200, 68 sessions).
    tol = np.maximum(2e-3, 3 * np.sqrt(both["fair_K"] * (1 - both["fair_K"]) / 20_000))
    below = both[both["fair_cont"] < both["fair_K"] - tol]
    assert len(below) == 0, f"{len(below)} rows where the continuous bound is below fair_K"


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
    assert (pending["fair_K"] == 1.0).all()
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
    first_cycle = pd.Timestamp(data.cycle_start(sorted(fair["cycle_id"])[0]))
    yes = venue[(venue["result"] == "Yes") & (venue["settled_at_utc"] >= first_cycle)]
    yes = yes[yes.index.isin(set(fair["market_id"]))]
    if yes.empty:
        pytest.skip("no YES resolution inside the replay window yet")
    meta = fair.drop_duplicates("market_id").set_index("market_id")
    missed = []
    for market_id, row in yes.iterrows():
        m = meta.loc[market_id]
        start = data.watch_start_for(market_id, m["deadline_utc"], m["period"])
        if pd.isna(start):
            continue
        seen = data.touched_between(m["ticker"], float(m["barrier"]),
                                    m["contract_type"] == "hit_up", start,
                                    row["settled_at_utc"] + pd.Timedelta(hours=1))
        if seen is False:
            missed.append((market_id, m["ticker"], m["contract_type"], float(m["barrier"])))
    rate = len(missed) / len(yes)
    assert rate <= MAX_VENUE_DISAGREEMENT, (
        f"{len(missed)} of {len(yes)} venue YES resolutions have no print on our bars: {missed[:5]}")


def test_fair_values_do_not_jump_between_cycles_without_a_reason(fair):
    """Same market, consecutive cycles, spot within 0.5% and the same session:
    `fair_K` should not move by more than 0.25. Jumps mean the smile is being
    built from a different set of contracts cycle to cycle (a filter on the
    edge) or the expiry pairing flipped."""
    ordered = fair[fair["fair_K"].notna() & ~fair["touch_pending"]].sort_values(["market_id", "cycle_id"])
    g = ordered.groupby("market_id")
    ordered["prev_fair"] = g["fair_K"].shift()
    ordered["prev_spot"] = g["spot"].shift()
    ordered["prev_T"] = g["T_sessions"].shift()
    same = ordered[ordered["prev_fair"].notna()
                   & ((ordered["spot"] / ordered["prev_spot"] - 1).abs() < 0.005)
                   & (np.floor(ordered["T_sessions"]) == np.floor(ordered["prev_T"]))]
    if same.empty:
        pytest.skip("no consecutive cycle pairs yet")
    jumped = same[(same["fair_K"] - same["prev_fair"]).abs() > MAX_FAIR_JUMP]
    share = len(jumped) / len(same)
    assert share <= MAX_FAIR_JUMP_SHARE, (
        f"{len(jumped)} of {len(same)} consecutive pairs jumped more than {MAX_FAIR_JUMP}: "
        f"{jumped[['ticker', 'barrier', 'cycle_id', 'prev_fair', 'fair_K']].head().to_dict('records')}")


def test_the_store_matches_what_the_engine_computes_today(fair, dates):
    """Reprice the newest stored cycle and compare. A difference means the
    engine code changed after the store was written and the models have been
    replaying stale fair values - reprice with `run_fair.py --date`."""
    newest = sorted(set(fair.loc[fair["date"] == dates[-1], "cycle_id"]))[-1]
    frame, stats = engine.price_cycle(newest)
    stored = fair[fair["cycle_id"] == newest][["market_id", "fair_K", "touch_pending"]]
    merged = stored.merge(frame[["market_id", "fair_K", "touch_pending"]], on="market_id",
                          suffixes=("_store", "_now"))
    both = merged[merged["fair_K_store"].notna() & merged["fair_K_now"].notna()]
    diff = (both["fair_K_store"] - both["fair_K_now"]).abs().max()
    assert diff < MAX_REPRICE_DIFF, f"store vs fresh reprice of {newest}: max |dfair_K| = {diff:.4g}"
    assert (merged["touch_pending_store"].astype(bool) == merged["touch_pending_now"].astype(bool)).all()
    assert stats["iv_checked"] > 0
    rate = stats["iv_disagreements"] / stats["iv_checked"]
    assert rate <= MAX_IV_DISAGREEMENT, f"IV disagrees with Cboe on {rate:.1%} of liquid strikes"


def test_an_earnings_gap_is_always_repaired_or_excluded(fair, fills):
    gap = fair[fair["earnings_gap"] & fair["fair_K"].notna() & ~fair["touch_pending"]]
    if gap.empty:
        pytest.skip("no earnings-gap market-cycles yet")
    unrepaired = gap[gap["vol_source"] != "pre_earnings_flat"]
    keyed = set(zip(unrepaired["cycle_id"], unrepaired["market_id"]))
    option_models = fills[fills["model"].isin(["edge_taker", "factor_neutral", "balanced_book", "direction_balanced", "scenario_hedged"])]
    traded = option_models[[(c, m) in keyed for c, m in
                            zip(option_models["cycle_id"], option_models["market_id"])]]
    assert traded.empty, f"{len(traded)} option-signal fills on an unrepaired earnings gap"


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
    fee = 0.04 * np.minimum(fills["price"], 1 - fills["price"]) * fills["shares"]
    assert np.allclose(fills["fee_usd"], fee, atol=1e-6)
    assert np.allclose(fills["cost_usd"], fills["price"] * fills["shares"] + fee, atol=1e-6)
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
        start = data.watch_start_for(f.market_id, f.deadline_utc, f.period)
        if pd.isna(start):
            continue
        if data.touched_between(f.ticker, float(f.barrier), f.contract_type == "hit_up",
                                start, f.filled_at_utc) is True:
            late.append((f.model, f.ticker, f.contract_type, float(f.barrier), str(f.filled_at_utc)))
    assert not late, f"{len(late)} fills after the barrier had printed: {late[:5]}"


def test_one_position_per_market_per_direction_per_model(fills):
    """MODELS 4.2: no averaging down, never both sides of one market."""
    sides = fills.groupby(["model", "market_id"])["outcome_index"].nunique()
    assert (sides <= 1).all(), f"both sides held: {sides[sides > 1].index.tolist()[:5]}"
    cycles = fills.groupby(["model", "market_id", "outcome_index"])["cycle_id"].nunique()
    assert (cycles <= 1).all(), f"re-entered without the flag: {cycles[cycles > 1].index.tolist()[:5]}"


def test_confirmed_fills_are_exactly_the_ones_whose_level_survived(fills):
    checked = fills[fills["checked"].astype(bool)]
    if checked.empty:
        pytest.skip("no fill has had a next cycle yet")
    expected = checked["next_level_size"] >= checked["shares"] - 1e-9
    assert (checked["confirmed"].astype(bool) == expected).all()
    unchecked = fills[~fills["checked"].astype(bool)]
    assert not unchecked["confirmed"].astype(bool).any(), "an unchecked fill was marked confirmed"


def test_every_position_respects_its_model_s_caps_at_every_cycle(marks):
    """From the per-cycle marks: cost per market, per ticker and per direction
    against MODELS 4.4 / section 5 caps, for every model, every cycle."""
    if marks.empty:
        pytest.skip("no marks yet")
    bad = []
    for model, group in marks.groupby("model"):
        cap_yes, cap_no, ticker_frac, direction_frac = CAPS_BY_MODEL.get(
            model.split("_ev")[0], CAPS_BY_MODEL["random_control"])
        per_market = group.groupby(["cycle_id", "market_id", "outcome_index"])["cost_usd"].sum()
        for (cycle, market, outcome), cost in per_market.items():
            cap = cap_yes if outcome == YES else cap_no
            if cost > cap + 1e-6:
                bad.append((model, "market", market, cycle, round(cost, 2)))
        per_ticker = group.groupby(["cycle_id", "ticker"])["cost_usd"].sum()
        over = per_ticker[per_ticker > ticker_frac * BANKROLL + 1e-6]
        bad += [(model, "ticker", t, c, round(v, 2)) for (c, t), v in over.items()]
        group = group.assign(direction=[sim.direction_of(ct, int(o)) for ct, o in
                                        zip(group["contract_type"], group["outcome_index"])])
        per_direction = group.groupby(["cycle_id", "direction"])["cost_usd"].sum()
        over = per_direction[per_direction > direction_frac * BANKROLL + 1e-6]
        bad += [(model, "direction", d, c, round(v, 2)) for (c, d), v in over.items()]
    assert not bad, f"{len(bad)} cap breaches: {bad[:5]}"


def test_settlements_agree_with_the_venue_and_with_arithmetic(settlements, venue):
    if settlements.empty:
        pytest.skip("nothing settled yet")
    for s in settlements.itertuples():
        row = venue.loc[s.market_id]
        assert str(row["result"]) == str(s.result), (s.model, s.market_id)
        won = (s.result == "Yes") if s.outcome_index == YES else (s.result == "No")
        assert s.payout_per_share == (1.0 if won else 0.0), (s.model, s.market_id)
        assert abs(s.pnl_usd - (s.payout_usd - s.cost_usd)) < 1e-6
        assert s.settled_at_utc >= s.entry_at_utc, (s.model, s.market_id)
        if s.outcome_index == NO:
            assert s.cash_back_at_utc >= s.settled_at_utc


# ========================================================= 4. model behaviour


def _fair_row(keyed, cycle_id, market_id):
    try:
        return keyed.loc[(cycle_id, market_id)]
    except KeyError:
        return None


def test_option_models_only_took_edges_that_cleared_the_bar(fills, fair):
    """A, B and B-simple: the recorded fair is the side-correct conservative
    one, the market was neither deep OTM nor chainless, and the price paid
    cleared MODELS 5.1's EV and ROI bars against that fair."""
    keyed = _fair_at(fair)
    option = fills[fills["model"].str.startswith(("edge_taker", "factor_neutral", "balanced_book", "direction_balanced", "scenario_hedged"))]
    if option.empty:
        pytest.skip("no option-signal fills yet")
    bad = []
    for f in option.itertuples():
        row = _fair_row(keyed, f.cycle_id, f.market_id)
        if row is None:
            bad.append((f.model, f.market_id, "no fair row")); continue
        k, atm = float(row["fair_K"]), float(row["fair_atm"])
        expect = min(k, atm) if f.outcome_index == YES else max(k, atm)
        if not np.isfinite(expect) or abs(expect - f.fair) > 1e-9:
            bad.append((f.model, f.market_id, f"fair {f.fair} != {expect}")); continue
        if bool(row["deep_otm"]) or bool(row["no_chain"]):
            bad.append((f.model, f.market_id, "deep_otm or no_chain")); continue
        win = expect if f.outcome_index == YES else 1.0 - expect
        cost = f.price + 0.04 * min(f.price, 1 - f.price)
        dear = f.price > 0.90
        ev_bar, roi_bar = (0.0, 0.02) if dear else (0.03, 0.08)
        if "_ev" in f.model:
            continue        # a tagged sweep runs at its own bar
        if win - cost < ev_bar - 1e-9 or (win - cost) / cost < roi_bar - 1e-9:
            bad.append((f.model, f.market_id, f"win {win:.3f} cost {cost:.3f}"))
    assert not bad, f"{len(bad)} option-signal fills that did not clear the bar: {bad[:5]}"


def test_empirical_longshot_traded_only_its_two_shapes(fills, fair, asks):
    """C: NO only when the YES ask was at most 12c, the empirical frequency at
    most half of it and the option engine not above the ask; YES only between
    15c and 50c with the empirical frequency at least 4c over cost."""
    c = fills[fills["model"].str.startswith("empirical_longshot")]
    if c.empty:
        pytest.skip("no empirical_longshot fills yet")
    keyed = _fair_at(fair)
    best = (asks[asks["depth_rank"] == 0]
            .set_index(["cycle_id", "market_id", "outcome_index"])["price"])
    bad = []
    for f in c.itertuples():
        row = _fair_row(keyed, f.cycle_id, f.market_id)
        if row is None or f.fair_source != "empirical" or abs(float(row["fair_empirical"]) - f.fair) > 1e-9:
            bad.append((f.market_id, "fair is not the empirical frequency")); continue
        emp = f.fair
        if f.outcome_index == NO:
            yes_ask = float(best.get((f.cycle_id, f.market_id, YES), np.nan))
            fair_k = float(row["fair_K"])
            if not (yes_ask <= 0.12 and emp <= 0.5 * yes_ask
                    and not (np.isfinite(fair_k) and fair_k > yes_ask)):
                bad.append((f.market_id, f"NO with yes_ask {yes_ask}, emp {emp:.3f}, fair_K {fair_k:.3f}"))
        else:
            cost = f.price + 0.04 * min(f.price, 1 - f.price)
            if not (0.15 <= f.price <= 0.50 and emp - cost >= 0.04 - 1e-9):
                bad.append((f.market_id, f"YES at {f.price} with emp {emp:.3f}"))
    assert not bad, f"{len(bad)} fills outside C's two shapes: {bad[:5]}"


def test_factor_neutral_is_inside_its_beta_band_whenever_it_trades(fills, marks):
    """MODELS 5.2 as implemented (P4 deviation 4): an entry band. At every cycle
    in which B filled, the book it left behind is within 5% of bankroll of
    beta-dollar-delta, with 10% tolerance for the projection."""
    b_fills = fills[fills["model"] == "factor_neutral"]
    if b_fills.empty:
        pytest.skip("factor_neutral has not filled yet")
    b_marks = marks[marks["model"] == "factor_neutral"]
    net = b_marks.groupby("cycle_id")["beta_dollar_delta"].sum()
    at_fill = net.reindex(sorted(set(b_fills["cycle_id"]))).dropna()
    band = 0.05 * BANKROLL * BAND_TOLERANCE
    over = at_fill[at_fill.abs() > band]
    assert over.empty, f"factor_neutral outside its band right after filling: {over.round().to_dict()}"


def test_balanced_book_never_grows_a_quadrant_past_the_smallest_plus_slack(fills, marks):
    """MODELS 5.2b: after its fills in a cycle, the quadrant it filled into is
    at most the smallest quadrant plus $500."""
    bb_fills = fills[fills["model"] == "balanced_book"]
    if bb_fills.empty:
        pytest.skip("balanced_book has not filled yet")
    bb_marks = marks[marks["model"] == "balanced_book"].copy()
    bb_marks["quadrant"] = [sim.quadrant_of(ct, int(o)) for ct, o in
                            zip(bb_marks["contract_type"], bb_marks["outcome_index"])]
    bad = []
    for cycle_id, group in bb_fills.groupby("cycle_id"):
        totals = bb_marks[bb_marks["cycle_id"] == cycle_id].groupby("quadrant")["cost_usd"].sum()
        totals = totals.reindex(["long_yes", "short_yes", "long_no", "short_no"], fill_value=0.0) \
            if set(totals.index) <= {"long_yes", "short_yes", "long_no", "short_no"} else totals
        smallest = float(totals.min()) if len(totals) else 0.0
        for ct, o in set(zip(group["contract_type"], group["outcome_index"])):
            q = sim.quadrant_of(ct, int(o))
            if float(totals.get(q, 0.0)) > smallest + 500.0 + 1.0:
                bad.append((cycle_id, q, round(float(totals.get(q, 0.0)), 2), round(smallest, 2)))
    assert not bad, f"balanced_book quadrant past smallest + slack after filling: {bad[:5]}"


def test_the_random_control_carries_no_forecast(fills):
    control = fills[fills["model"].str.startswith("random_control")]
    if control.empty:
        pytest.skip("no control fills yet")
    assert control["fair"].isna().all(), "the control recorded a fair value"


def test_the_daily_report_exists_for_the_newest_priced_date_and_warns(dates):
    path = sim.REPORTS / f"daily_{dates[-1]}.md"
    assert path.exists(), f"no daily report for {dates[-1]}: run model/daily.py"
    text = path.read_text()
    assert len(text.splitlines()) <= 120
    assert "not" in text.lower() and "result" in text.lower(), "the selection warning is missing"
