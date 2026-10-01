# Handoff

One section per delegated pass. Files created or changed, commands to
reproduce, every measured number relied on, every deviation from
`model/MODELS.md`, open questions. Nothing else.

---

## P1 2026-09-18 — historical calibration, February–August 2026

### Files

| path | what it is |
|---|---|
| `data/src/poly_backfill.py` | new. Fetches closed Feb–Aug 2026 Polymarket ladders by slug, writes `poly_markets` / `resolutions` / `poly_price_history`, and appends 1-hour `underlying_bars`. Idempotent via `store/meta/poly_backfill_state.json`. |
| `model/calibration.py` | new. Pure functions: price and time bucketing, Wilson and cluster-robust intervals, Brier, EWMA vol, continuous one-touch, the MODELS 3.5 empirical touch table, cost/PnL of one leg. |
| `model/run_calibration.py` | new. Assembles the point-level table from Parquet and writes the report. No network. |
| `model/tests/conftest.py` | new. Puts `model/` and `data/src/` on the path, suppresses the collector's file logger (same reason as `data/tests/conftest.py`). |
| `model/tests/test_calibration.py` | new. 49 tests. |
| `model/tests/test_run_calibration.py` | new. 11 tests. |
| `data/tests/test_backfill.py` | new. 14 tests. |
| `model/reports/calibration_2026-09-18.md` | new. The report. |
| `requirements.txt` | changed: added `numpy==2.5.3` (see Dependencies). |

Nothing in `data/src/` was modified. No schema changed. Nothing was committed.

### Reproduce

```
.venv/bin/python data/src/poly_backfill.py --tiers tier1,tier2   # ~9 min, network
.venv/bin/python model/run_calibration.py                        # ~3 min, offline
.venv/bin/python -m pytest data/tests model/tests -q             # 214 passed
```

A second `poly_backfill.py` run makes **0 requests and writes 0 rows** (verified).
`run_calibration.py` is deterministic apart from section 5.2's control, which is
seeded (`seed=11`).

### Measured

**API behaviour** (all verified 2026-09-18 against the live endpoints):

- `prices-history?interval=max` returns an **empty history** for a token whose
  market has closed. Checked on PLTR August tokens, both outcomes, fidelity 1
  and 60, and on an AAPL weekly. The data is reachable only through explicit
  `startTs`/`endTs`.
- That window is capped: **15 days is accepted, 16 days returns
  `{"error":"invalid filters: 'startTs' and 'endTs' interval is too long"}`**.
  The backfill uses 14-day windows.
- On a market where both tokens were pulled, every timestamp present in both
  series had **YES + NO = 1.0 exactly** (max deviation 0.0), and the two series
  carry the same hourly points stamped a few seconds apart. Only the YES token
  is fetched; this halves the request count and loses nothing.
- Four slug shapes cover the equity ladders:
  `what-price-will-{t}-hit-in-{month}-2026`, `{t}-above-in-{month}-2026`,
  `will-{t}-hit-week-of-{month}-{day}-2026` ({day} = the Monday).
- Older events use different wording — "Will NVIDIA reach $272 in February?",
  no parenthesised ticker, no "(HIGH)". `poly_parse`'s question patterns miss
  it; its `groupItemTitle` fallback ("↑ $272") catches it and the event title
  supplies the ticker, so `market_row` types them correctly with no change.
- Throughput: 12,377 requests in 540 s = **22.9 req/s at 8 workers**, which
  matches the ~25 req/s server-side cap `markets.yaml` records for Kalshi.
  **No 429 was ever returned.**

**Backfill run** (19 tickers, tier1 + tier2):

| | |
|---|---|
| slugs probed | 855 (561 returned an event, 294 returned nothing) |
| empty by kind | 188 weekly, 72 close-above, 34 monthly-hit |
| found by kind | 401 weekly, 99 monthly-hit, 61 close-above |
| markets written | 8,392 (`poly_markets` and `resolutions`) |
| price-history rows | 2,209,968, one series per market's YES token |
| hourly bars | 91,779 rows, 19 tickers |
| requests / runtime | 12,377 / 540 s, plus 29 s of yfinance |
| failures | 0 |

Events by deadline month: 10 (Feb), 20 (Mar), 79 (Apr), 96 (May), 95 (Jun),
120 (Jul), 103 (Aug), 38 (Sep, fetched but excluded from the report). The Feb
and Mar counts are genuinely small — Polymarket had far fewer equity ladders
then, consistent with the ~90% shrinkage in `MARKETS.md`.

Slugs that returned nothing are listed in full in
`data/store/meta/poly_backfill_state.json` under `empty_slugs`. They are not
errors: most tickers simply had no weekly ladder in most weeks.

**Report** (`model/reports/calibration_2026-09-18.md`):

- 7,113 resolved hit markets, 1,596,109 hourly price points, 19 tickers,
  deadline months 2026-02 … 2026-08.
- 1,031 markets dropped for a September deadline.
- Base rate 0.366 per market, 0.213 per price point.
- Headline: **every price bucket below 65c is overpriced on the YES side**,
  by 1.1c at 1.4c up to 8.8c at 44.5c. Clustered intervals exclude zero
  everywhere except `>65c`.
- Brier: empirical fair 0.1030, continuous one-touch 0.1047, market price
  0.1091, base rate 0.1579. The no-options empirical model is the best of the
  three. *(Corrected in the 2026-09-18 review: the numbers first written here
  — 0.1083 / 0.1104 / 0.1229 / 0.1676 — predate the placeholder exclusion and
  never matched the report on disk.)*
- "Buy NO when YES ≤ 10c": −3.2% over 4,708 bets, against a price-matched
  random control of −1.5%. The rule is worse than random.
- "Buy YES when empirical − price ≥ 5c": +10.3% over 4,688 bets, against a
  control of −24.5% (sd 1.4%, 20 draws). 400 ticker-weeks; no ticker
  contributes more than 22% of PnL. *(Corrected in the 2026-09-18 review from
  +13.1% over 4,780 bets: the spot at each price point was the close of the
  hourly bar containing it, a print up to an hour in the point's future.)*

### Deviations from MODELS.md and from the prompt

1. **Price history is not fetched with `collect_price_history`.** The prompt
   specified it; it cannot work here, because `interval=max` returns nothing
   for a closed market (measured above). `poly_backfill.py` windows the request
   itself. `data/src/poly_books.py` was not touched, so the live collector is
   unaffected; a token backfilled this way is written with
   `interval='backfill_window'`, which is how the two sources are told apart.

2. **`Http` is not used for the backfill's requests.** `Http` retries a 429 five
   times with backoff, and the prompt required stopping on the first one. The
   `Fetcher` class reuses `Http`'s session (so connection pooling, the User-Agent
   and the shared failure list are unchanged) and reimplements the retry ladder
   with 429 promoted to a fatal `RateLimited`.

3. **1-hour `underlying_bars` were added.** MODELS 3.5 implies spot comes from
   `underlying_bars`, which holds 5 years of daily and 60 days of 5-minute bars
   — so for Feb–Aug the only available spot was the previous session's close,
   up to a full session stale at an hourly price point. yfinance serves ~3 years
   of 1-hour regular-session bars (PLTR: 5,073 bars from 2023-10-19), which is
   the same window Pyth candles see. Appended under `interval='1h'`; no schema
   change, no existing row touched. Spot at a price point is now at most one
   hour stale instead of one session.

4. **The touch table takes a *daily* sigma, not the annualised one.** MODELS 3.5
   writes `S_0 · exp(±k · sigma_realised · sqrt(h))` with `h` in sessions, which
   only makes sense for a daily sigma; `ewma_vol` returns an annualised number
   because `one_touch` needs it that way. Feeding the annualised number in makes
   every threshold √252 = 15.9 sigma too far and the whole table reads 0.000.
   This happened during development and is now pinned by
   `test_touch_table_expects_daily_sigma_not_annualised`.

5. **Touch tables are built only from sessions before 2026-02-01.** MODELS 3.5
   says "5 years of daily bars" without saying when. Using the full 5 years
   would leak the evaluation window into the model. Every scored point is
   therefore out of sample. The "last 250 sessions" regime-drift variant in
   MODELS 3.5 is not built; it would be in-sample here.

6. **Points quoted at exactly 0.5000 are excluded from the headline tables and
   from both trading rules** (report section 6.1). 160,508 points — 10.1% of the
   sample — sit at that one tick. They have median barrier distance k = 0.92
   against 0.56 for the rest of the 50-65c bucket, median empirical fair 0.31
   against 0.52, and with under four hours to the deadline they resolve YES
   **4.0%** of the time while marked at 50c. That is the venue marking an hour
   with no book, not a market saying 50/50 — the same class of artifact as
   FINDINGS 4's untraded Cboe contracts. Including them moves the 50-65c gap
   from −0.028 to −0.077, i.e. they were most of that bucket's apparent
   mispricing. Report section 2.1 repeats the headline with them left in.

7. **Points whose spot is already beyond the barrier are excluded from sections
   4 and 5** (25,973 points, 1.6%), per MODELS 3.1's "skip the market if S_book
   is already beyond B". They are kept in the reliability tables, which are
   about quotes rather than fair values, and reported separately in section 6.2.

8. **A cluster-robust interval was added next to the binomial one**, and the
   report tells the reader to use it. The prompt asked for a binomial 95%
   interval; on 1.6M points drawn from 7,113 markets, where every hour of a
   market shares that market's single outcome, the binomial interval is ±0.001
   and is a statement about the sample size rather than about what is known.
   The clustered interval is 5–25× wider. Both are printed; the conclusions in
   the report are stated against the clustered one.

9. **A price-matched random control was added** (report section 5.2), which the
   prompt did not ask for but MODELS 4.5 requires of every model. It is what
   turns "+13.1%" into a claim: the same side, the same number of bets, the same
   price buckets, random markets, returns −24.0%.

10. **PnL months are the deadline's month in ET, not the raw `end_date` month.**
    A monthly ladder ends at `2026-09-01T03:59:59Z`, which is 2026-08-31 23:59
    ET; grouping on the UTC string would file every monthly market one month
    late. The deadline itself is the last NYSE session close at or before
    `end_date`, not `end_date`, because a hit market can only resolve during
    regular hours.

11. **Weekly ladders for the week of 2026-08-31 were fetched but not scored.**
    Their deadline is 2026-09-04, and the prompt excludes September. They are on
    disk and fully resolved, so a later pass can include them cheaply.

### Dependencies

`numpy==2.5.3` added to `requirements.txt`. It was already installed as a
transitive dependency of pandas and pyarrow, but `model/calibration.py` imports
it directly and an undeclared direct import is how a working environment turns
into an unreproducible one. No other dependency was added; `scipy` was
deliberately not added — the one function needed from it (the normal CDF) is
four lines over `math.erf`.

### Open questions

1. **Is the +10.3% (was +13.1%, see review) fillable?** Every entry is a mid plus half the *median*
   spread, with no book, no depth and no queue. FINDINGS 7 measured $52–$719 of
   depth per market and a fill running 0.114 → 0.591 for $0 → $500 on one
   ladder. The empirical signal fires ~23 times a session across 19 tickers, so
   the per-market size is small, but "small" is not "measured". The live
   `poly_books` collection is now the only way to answer this, and it is the
   single most valuable next measurement.

2. **Why is the signal concentrated in April, June and July?** +34.4%, +17.4%,
   +18.5%, against +1.1% (Feb), +0.9% (Mar), +5.0% (May), −1.4% (Aug). With
   ~400 ticker-weeks this is within noise, but it is also the shape a
   volatility-regime effect would have: the empirical table is a 2021–2025
   average and 2026 does not have to look like it. Re-running the table on a
   rolling window, out of sample, would separate the two.

3. **`hit_down` is far more overpriced than `hit_up`** (at 35-50c: −0.123
   against −0.055). That is a crash-insurance premium, and it is the direction
   MODELS 5.3 wants to *sell*. The paper PnL says selling it does not clear the
   spread; whether a maker could earn it is the question FINDINGS 1 left open,
   and it needs the trade tape.

4. **Near-expiry quotes look unreliable.** A contract marked at 48.5c with under
   four hours left resolves YES 3.0% of the time (787 points, 356 markets). Some
   of that is the placeholder problem in a different disguise. Before anyone
   trades a "sell the decayed contract" rule, those quotes need a book behind
   them.

5. **Nothing here has been checked against the option-implied fair.** P2 builds
   that engine; the same 1.6M points and outcomes are now on disk to score it
   against, which makes "does implied vol beat the historical frequency"
   a one-line comparison rather than a new project.

---

## P2 2026-09-18 — fair-value engine

### Files

| path | what it is |
|---|---|
| `model/data.py` | new. The only module in `model/` that reads Parquet. Session filter, the 2026-09-17 exclusion, books, Cboe chain, spot at book time, rates, earnings, resolutions-up-to-a-time. |
| `model/sessions.py` | new. NYSE session arithmetic: `sessions_remaining` (fractional), `deadline_close`. Shared by the engine and P1's report so the two agree on what a two-day barrier is. |
| `model/fair.py` | new. Black-Scholes, IV inversion, the smile, variance interpolation, the continuous one-touch, the regular-hours Monte Carlo, numerical delta, MODELS 3.4 flags. Pure: no store, no network. |
| `model/empirical.py` | new. MODELS 3.5's touch table and `f`. Holds `ewma_vol` / `touch_table` / `TouchTable`, moved out of P1's `calibration.py`. |
| `model/engine.py` | new. `price_cycle(cycle_id)`: the loop that joins data to maths and owns the three alignment traps. |
| `model/fair_schema.py` | new. Explicit Arrow schema for the engine's output, plus `write_fair`. Not called `schemas` — see deviation 1. |
| `model/run_fair.py` | new. `--date YYYY-MM-DD`; prices every session cycle of the date, replaces that date's partition, prints the report. |
| `model/calibration.py` | changed: the barrier maths and touch table moved to `fair.py` / `empirical.py` and are re-exported, so P1's report keeps one import. |
| `model/run_calibration.py` | changed: now imports the shared session calendar instead of its own copy. |
| `model/tests/test_fair.py` | new. 29 tests, including MODELS 3.3's four. |
| `model/tests/test_empirical.py` | new. 12 tests (8 moved from `test_calibration.py`, 4 new for `f`). |
| `model/tests/test_sessions.py` | new. 9 tests, moved from `test_run_calibration.py` plus deadlines. |
| `model/tests/test_engine.py` | new. 13 integration tests on cycle `20260916T155117Z-5639a2`. |
| `model/tests/test_fair_schema.py` | new. 6 tests. |
| `.gitignore` | changed: `model/store/` added. |

`data/` was not touched. No new dependency. Nothing committed.

### Reproduce

```
.venv/bin/python model/run_fair.py --date 2026-09-16      # 130 s, offline
.venv/bin/python -m pytest data/tests model/tests -q      # 263 passed
```

A rerun replaces the date's partition rather than appending to it, and the
Monte Carlo draws are seeded, so two runs give identical numbers (asserted by
`test_pricing_is_reproducible`).

### Measured, 2026-09-16

**Throughput.** 21 session cycles, 13,980 market-cycles, 680 distinct markets,
**6.1 s median per cycle** and 130 s for the day, at 20k antithetic paths.
MODELS 3.3 asked for ~600 markets in under a minute.

**Alignment.** Median `quote_age_s` **815 s (13.6 min)**, which is the Cboe
delay the README measured and the reason the correction is mandatory rather
than optional. Median `book_age_s` 22 s; 7.6% of market-cycles carry a book the
venue has not changed in over 15 minutes.

**IV against Cboe.** 61,002 liquid-strike checks, **median gap 0.0019**,
8,060 (13.2%) past 0.01. FINDINGS section 4 said ours matched Cboe to within
0.008 by hand; this reproduces that. The disagreements are concentrated in the
chains FINDINGS section 4 already named as artifacts — the five worst on the
15:51Z cycle are all EWY, whose strikes trade 27% of the time.

**`f`, the intraday variance share**, from 5 years of daily open/close:

| | f |
|---|---|
| EWY | 0.327 |
| META 0.450, NFLX 0.533, AMZN 0.537, MU 0.556, GOOGL 0.577, MSFT 0.596 | 0.45-0.60 |
| NVDA 0.634, PLTR 0.644, AAPL 0.651, SPY 0.656, COIN 0.667, TSLA 0.670, ABNB 0.672, MSTR 0.675, HOOD 0.687 | 0.63-0.69 |
| OPEN 0.750, RKLB 0.753, SPX 0.755, SPCX 0.800 | 0.75-0.80 |
| XAGUSD 0.664, WTI 0.868, XAUUSD 0.872, DXY 0.920 | not equities |

MODELS 3.3 expected 0.5-0.7 for single names and higher for SPY. The single
names land there; SPY is 0.656 and SPX 0.755. **EWY at 0.327 is the check that
the estimator is measuring something real**: a Korea ETF does most of its
moving while Seoul trades and the NYSE is shut, so two thirds of its variance
arrives as an overnight gap that Polymarket never watches.

**The monitoring correction — FINDINGS open question 5.1, now quantified.**
That question said the continuous formula overstates touch probability by an
unknown amount and "could delete half the edges". It does not. Across 7,146
priced, non-deep-OTM market-cycles the continuous formula is above the
monitored one **96.7% of the time**, by:

| fair_K | n | median fair_K | median fair_cont | excess | ratio |
|---|---:|---:|---:|---:|---:|
| 0-5c | 762 | 0.038 | 0.044 | +0.005 | 1.14 |
| 5-10c | 1,417 | 0.071 | 0.082 | +0.008 | 1.12 |
| 10-20c | 1,410 | 0.141 | 0.156 | +0.012 | 1.08 |
| 20-35c | 1,199 | 0.259 | 0.281 | +0.015 | 1.06 |
| 35-50c | 961 | 0.423 | 0.443 | +0.020 | 1.05 |
| 50-65c | 717 | 0.564 | 0.593 | +0.022 | 1.04 |
| >65c | 680 | 0.742 | 0.772 | +0.022 | 1.03 |

So: **0.5c to 2.2c in absolute terms, 3% to 14% in relative terms, largest
proportionally on the longshots.** FINDINGS 3.1 measured a *median* edge of
0.9c across the 164 markets that screened positive, so the correction is large
enough to erase the median edge outright and too small to touch the 4.7-14c
ones in that table. Both halves of that sentence matter, and neither was known
before today.

**MODELS 3.3 also predicted the correction would be largest "for `hit_down`
YES near the money". It is not.** By direction: `hit_down` median excess
+0.0145 (ratio 1.068), `hit_up` +0.0120 (ratio 1.081). Absolutely larger for
`hit_down`, proportionally larger for `hit_up`, and the difference either way
is small. The prediction was a guess and the data does not support it.

**Flags, share of the 13,980 market-cycles:** `deep_otm` 46.7%,
`exact_expiry` 55.7%, `clamped_strike` 25.3%, `no_chain` 19.8%,
`single_expiry` 18.1%, `stale_book` 7.6%, `clamped_atm` 4.8%,
`earnings_inside` 2.3%, `touch_pending` 0.3%.

Two of those deserve reading now rather than in P4:

* **`deep_otm` is 46.7%.** Nearly half the hit-market universe sits more than
  2.5 standard moves from spot, and MODELS 3.4 forbids trading those on the
  option signal. The tradeable universe is about 350 markets a cycle, not 665.
* **`no_chain` is 19.8%, and it is entirely the commodity and FX ladders** —
  WTI (756), DXY (589), XAUUSD (588), XAGUSD (560), NG (273). They have
  Polymarket barriers and no Cboe equity chain, exactly as MODELS section 1
  expects. No equity market failed for want of a chain.

### Deviations from MODELS.md and from the prompt

1. **`model/schemas.py` is called `model/fair_schema.py`.** `data/src/schemas.py`
   already owns the name `schemas`, both directories go onto `sys.path` as flat
   module trees, and two modules of one name cannot coexist in one process:
   whichever loads first wins for everything and the other fails at its one
   distinctive call. Running `pytest data/tests model/tests` in a single process
   is enough to trigger it, and it did — eight collection errors reading
   `cannot import name 'coerce' from 'schemas'`. There is no path ordering that
   satisfies both suites. Pinned by
   `test_the_engine_schema_does_not_shadow_the_collectors`.

2. **`model/engine.py` exists**, which the prompt did not list. `fair.py` is
   pure maths with no knowledge of the store and `data.py` is the store with no
   knowledge of the maths; the per-cycle loop needs both, plus `empirical`,
   `sessions` and `fair_schema`. Putting it in `fair.py` would have made the
   engine's unit tests depend on a Parquet store; putting it in `run_fair.py`
   would have put the pricing loop inside the CLI.

3. **Two clocks, not one, and this changes every implied vol.** MODELS 3.2 does
   not say what `T` the IV inversion uses. Measured on 2026-09-16 against
   Cboe's own `source_iv`: calendar time from the quote to the expiry's 16:00
   ET close reproduces it to a median of 0.003-0.009 across four expiries,
   while a trading-day convention is out by 0.042 on the near expiry and 0.114
   if the expiry day is counted. So the engine **inverts on the calendar clock,
   carries total variance `w = sigma^2 T`, and divides by session time at the
   end**. Using session time for the inversion made every PLTR weekly vol read
   0.47 instead of 0.60 and made the IV check fire on 100% of contracts. Total
   variance is preserved either way, so the touch probabilities are unchanged;
   what changes is whether the check against Cboe means anything.

4. **`pick_expiries` compares at the expiry's session close, not its date.** A
   Friday weekly barrier deadlines at the Friday close, which *is* the Friday
   expiry's own moment. Comparing dates put the one expiry that matches the
   payoff exactly on the wrong side of the deadline: a weekly barrier was
   interpolated between its own expiry and the next week's, instead of being
   priced off its own. 55.7% of market-cycles are `exact_expiry` once this is
   right.

5. **`exact_expiry` was added to MODELS 3.4's flag list.** When the deadline is
   an expiry, there is nothing to interpolate and nothing lost by it — that is
   the best case, not a warning, and without the flag a model reading
   `single_expiry` as a defect would discard its best-matched markets.
   `spread_iv` (which MODELS 3.4 does list) and `stale_book`, `no_chain`,
   `touch_pending` are also carried.

6. **The Monte Carlo integrates the within-session crossing instead of sampling
   it.** MODELS 3.3 gives `P(cross | O, C)` as a formula; the engine multiplies
   it into a survival factor rather than drawing a uniform against it. That is
   conditional Monte Carlo — unbiased, and roughly an order of magnitude less
   variance for the same paths. Verified against the closed form at `f = 1`:
   bias +0.0001 at 200k paths, standard error 0.0011 at 20k.

7. **Draws are shared across markets and across the delta bump.** A single
   antithetic pool per cycle, seeded. This is not only for speed: at 20k paths
   the MC standard error is ~0.0035, which is the same size as the whole effect
   of a 0.5% spot bump, so two independent estimates would give a delta whose
   sign is a coin flip. Common random numbers make the difference deterministic
   (`test_delta_survives_the_monte_carlo_noise_it_would_otherwise_drown_in`).

8. **The smile is anchored on one reference time per ticker, not per market.**
   A ticker's markets are booked seconds apart; caching the smile under the
   first market to ask for it made the result depend on DuckDB's row order, and
   two runs of the same cycle differed in the fourth decimal. Found by
   `test_pricing_is_reproducible`, which is why it is there.

9. **The deadline is the last session close at or before `end_date`.** A
   monthly ladder carries `end_date = 2026-10-01T03:59:59Z`, which is 23:59 ET
   on 30 September — hours after the last candle anyone could touch a barrier
   on. Asserted against the calendar rather than against "20:00Z", because the
   December SPX ladders deadline at 21:00Z once the clocks go back.

10. **MODELS 3.3's test (c) is split in two.** "A barrier already inside the
    overnight gap gives ~1" is asserted deterministically — a pool whose every
    gap draw is +4 sd gives exactly 1.0, with `f` near zero so the touch can
    only have come from the gap. The statistical companion asserts the thing
    that is actually true and is more interesting: with only discrete opens and
    closes monitored, a driftless walk starting at the barrier's doorstep
    escapes `1/sqrt(pi*n)` of the time (the ballot problem), so over 8 sessions
    the monitored probability is ~0.82 while the continuous formula says >0.99.
    A naive "~1" threshold on that setup fails, and it should.

### Open questions

1. **`f` is the wrong shape for the commodity and FX ladders.** WTI 0.868,
   XAUUSD 0.872, DXY 0.920 are high because those markets trade nearly around
   the clock, so little variance arrives as a "gap" against a 09:30-16:00 ET
   window. But Polymarket does not resolve those barriers on the NYSE session
   either. The whole `f` construction assumes the monitored window is the NYSE
   session; for these it is not. They have no option chain and are not priced,
   so nothing is wrong today — but a model that later reaches for them needs
   the venue's actual monitoring window first, from the rules text in
   `poly_markets.rules_text`.

2. **Is `sigma_K` or `sigma_atm` the better predictor?** The engine produces
   both and MODELS 5.1 requires a market to be positive under both, which is a
   rule, not a measurement. P1 put 1.6M hourly prices and their outcomes on
   disk; scoring `fair_K` and `fair_atm` against those outcomes the way P1
   scored the empirical fair is now a small job, and it would replace that rule
   with a number. The median `spread_iv` is 0.158 on NFLX and 0.134 on MSTR,
   so on those names the choice is worth several cents of fair value.

3. **`deep_otm` at 46.7% may be too blunt.** MODELS 3.4's threshold of 2.5
   standard moves comes from one SPY example in FINDINGS 5.2. Half the universe
   is a lot to discard on one anecdote, and the same 1.6M-point sample could
   say where the option-implied fair actually stops predicting.

4. **The 15-minute delay is carried but not corrected.** `quote_age_s` is
   reported and the smile is anchored on `S_quote` so the moneyness lookup is
   consistent, but nothing adjusts for what the underlying did in those 13.6
   minutes beyond starting the touch model at the newer bar spot. FINDINGS 5.4
   measured PLTR's fair moving 0.39 to 0.70 on a 170 to 174 move, so this is
   not a rounding error on a fast day. Quantifying it needs the fair recomputed
   at the book spot against the same fair at the quote spot, per cycle — cheap,
   and not yet done.

5. **`stale_book` at 7.6% has no threshold behind it.** 900 s is one collection
   cycle, chosen because it is one collection cycle. Whether a book untouched
   for 15 minutes is actually unfillable is a question for the trade tape.

---

## P3 2026-09-18 — simulation harness

### Files

| path | what it is |
|---|---|
| `model/sim.py` | new. The replay-execute-resolve-report loop: `CycleState`, `Order`, `Position`, `Portfolio`, the ladder walk, the persistence check, settlement, metrics, the Markdown report, and `RandomControl`. Also `candidate_edges`, the MODELS 5.1 signal that A, B and B-simple share. |
| `model/factors.py` | new. Betas to SPY from a 250-session regression on bars strictly before the cycle; SPY's intraday move for model C's stop. |
| `model/ledger_schema.py` | new. Arrow schemas and writer for `fills`, `marks`, `resolutions`. |
| `model/data.py` | changed: added `fair_values(date)` and `fair_dates()`, the readers for P2's own output. Nothing else touched. |
| `model/tests/test_sim.py` | new. 59 tests. |
| `model/tests/test_factors.py` | new. 11 tests. |

Nothing in `data/` was modified. No collector schema changed. Nothing committed.

### Reproduce

```
.venv/bin/python -m pytest data/tests model/tests -q        # 374 passed
.venv/bin/python -c "import sys; sys.path.insert(0,'model'); sys.path.append('data/src'); \
  import sim; r = sim.run(sim.RandomControl(orders_per_cycle=8), dates=['2026-09-16']); \
  print(sim.write_run(r))"
```

The replay is deterministic given the store; the control is seeded (`seed=20260918`).

### Measured, 2026-09-16

**Throughput.** 21 priced session cycles replayed, **2.0 s per model** for the
whole day, 13 s for all five models plus the universe scoring. The window is
one session: `model/store/fair` holds only `date=2026-09-16`, 09-17 is dropped
by `data.in_session`, and 09-18 has two cycles and no fair values.

**The random control**, 8 orders per cycle (set from `edge_taker`'s activity):

| | dollars | count |
|---|---:|---:|
| orders proposed | $51,050.00 | 168 |
| allowed by the caps | $19,229.22 | |
| filled (optimistic) | $8,000.00 | 85 fills |
| filled (confirmed) | $3,565.23 | 35 fills |
| fees paid | $99.46 | |

**Optimistic 85 fills / $8,000; confirmed 35 fills / $3,565.** So 41% of fills
and **45% of filled dollars survive one cycle of the persistence check** — with
a 15-minute cadence, so this is a 15-minute persistence test and not a
millisecond one. Across all five models the confirmed share of dollars ranges
14.2% (`edge_taker`) to 56.8% (`factor_neutral`); the models that reach deepest
into the ladder keep the least. This is the single largest correction the
harness applies and it is larger than every modelled edge in FINDINGS 3.1.

**Where the money went instead of into positions.** For the control: fill rate
15.7% of dollars wanted, blocked by `direction_cap` on 88 orders, the book
running out on 42, `ticker_cap` on 17. The books are the binding constraint on
every model: median fill `depth_rank` is 0 for all five, meaning most orders
take the top level and stop.

### Deviations from MODELS.md and from the prompt

1. **`model/schemas.py` is `model/ledger_schema.py`**, for the reason P2's
   deviation 1 records: `data/src/schemas.py` owns the name and two modules of
   one name cannot coexist in one flat `sys.path`.

2. **The three ledger tables are in their own subdirectories**, not
   `model/store/ledger/<model>/*.parquet` as the prompt wrote. DuckDB's
   `read_parquet` glob errors when one file in a tree has a column its
   neighbours lack, and `fills`, `marks` and `resolutions` share nothing but
   `market_id`. They are at `<model>/<table>/part-*.parquet`.

3. **`Order.limit_price` is an all-in cost per share, not a price.** MODELS 4.2
   stops the walk where `price + fee >= fair - min_edge`, and MODELS 2 adds an
   ROI floor that binds on a different set of levels. Folding both into one
   number the model computes and the harness enforces keeps the stop rule in
   one place instead of four.

4. **`min_fill_usd = $1`, Polymarket's own minimum, and the whole walk is
   discarded if it comes to less.** Not in MODELS. Without it the fill count is
   dominated by $0.30 sweeps that no exchange would accept: the median top-of-
   book on this venue is $23 of YES and $54 of NO. An exchange rejects an
   undersized order, it does not shrink it, so the walk goes whole.

5. **Re-entry must be asked for explicitly (`Order.reenter`) and resets the
   reference fair.** MODELS 4.2 says "one market, one direction, per model, per
   lifetime (no averaging down), unless the model *explicitly* re-enters after
   fair moves by more than 10c". The first implementation allowed any repeat
   order once the fair had drifted 10c from the *original* entry and never
   moved that reference — which let a fair oscillating either side of it
   re-enter every cycle. Measured on 2026-09-16 before the fix: **five
   identical fills of 42.47 shares at $0.410 in market 4485830**, on a book
   that had not changed. No model in section 5 sets the flag, so all of them
   now hold one position per market per lifetime. This changed `edge_taker`'s
   filled dollars from $4,238 to $3,783.

6. **The cycle's clock is `max(fetched_at_utc)`, not `book_timestamp_utc`.**
   The latter is the venue's last change to a book and P2 measured it at up to
   4.9 days old; using it would decide which resolutions a model may see from
   the age of the stalest book in the cycle.

7. **A settlement sweep runs after the last cycle.** The replay settles as it
   goes, so cash returns in time to be re-spent; then, once every decision has
   been made, everything that has settled since is booked. That is not
   lookahead — no model saw it — and without it MODELS 4.3's resolved-only
   headline is empty on any window shorter than the markets' horizons, which is
   every window available today.

8. **A NO position's capital-days run to the deadline, its cash returns at
   settlement.** MODELS 4.4 says a 95c NO is money locked for up to a month and
   MODELS 4.3 says cash returns at `settled_at_utc`; both are implemented
   literally, so `capital_days` and `cash` can disagree by the gap between the
   deadline and the venue's mark. Pinned by
   `test_capital_days_run_to_the_deadline_for_a_no`.

9. **The portfolio runs on the optimistic ledger.** At the moment a model
   decides it cannot know whether the level it just took will still be there,
   so it must decide as though the fill happened. The confirmed ledger is a
   parallel accounting of the same fills, not a second simulation.

10. **`universe_scores` returns `(scores, sample)`.** See open question 1: the
    scores are meaningless in this window and the sample is the reason, so no
    caller can print the table without having been handed the reason not to.

11. **Betas fall back to 1.0, never 0.0**, for a ticker with under 60 sessions
    and for the commodity and FX ladders. A beta of 0 would tell model B a
    position carries no market risk. Duplicate daily bar times (P1's backfill
    appended hourly bars beside the collector's daily ones and `select
    distinct` keeps both when the closes differ in the last decimal) are
    deduped at the read, last write wins; before that they made the SPY join
    raise.

12. **No-lookahead is enforced by construction plus one accessor.** A
    `CycleState` is built from frames already sliced to its cycle; `books_at`
    and `fair_at` are the only route to another cycle and raise
    `LookaheadError` on anything at or after the current one, including the
    current one. `test_a_peeking_model_fails_the_run_rather_than_quietly_cheating`
    runs a model that peeks through a whole replay and asserts it raises.

### Dependencies

**None added.** `numpy`, `pandas`, `pyarrow`, `duckdb` and
`pandas_market_calendars` were already pinned by P1 and P2.

### Open questions

1. **There is no unbiased calibration sample in this window, and there cannot
   be one yet.** A hit market settles the instant it touches and otherwise
   waits for its deadline. Of 680 priced markets, 51 have settled and **0 have
   reached their deadline**; all 51 resolved `Yes`. So the base rate is 1.000,
   the Brier ranking is a ranking of which estimate is highest — `fair_cont`,
   MODELS 3.3's deliberate upper bound, comes top — and every resolved-only PnL
   in P4 measures which side of a touched market a model was on. The deadlines
   are 2026-09-18 (35 markets) and 2026-09-30 (16). Both reports say this
   above the tables; nothing here is a result until those dates pass.

2. **The persistence check is a 15-minute test because the cadence is 15
   minutes.** MODELS 4.2 says "the next cycle" and the next cycle is a quarter
   of an hour away, so what is being measured is whether a level survives 15
   minutes, not whether a taker would have hit it. It rejects 55% of filled
   dollars, which is too large a correction to leave resting on a cadence. The
   trade tape would settle it.

3. **Sub-cent tokens make the dollar caps the wrong unit.** `edge_taker` bought
   44,092 shares of a SPY `hit_down` NO for $166 at $0.001-$0.010, against a
   $500 per-market cap. Max loss is $166 so no risk limit was breached, but the
   position carried $1.75M of beta-dollar-delta on its own and is the whole of
   that model's 25,715%-of-bankroll peak. A cap in dollars paid does not cap
   the payoff; whether that matters depends on whether those levels are real,
   which is question 2 again.

4. **`RandomControl` takes the top of book plus 2c of slippage**, and its
   activity is set to `edge_taker`'s orders per cycle. Both are choices, not
   measurements. A control that walks as deep as the model it controls for
   would be a stricter test.

---

## P4 2026-09-18 — the models

### Files

| path | what it is |
|---|---|
| `model/models/__init__.py` | new. The package; re-exports the four classes. |
| `model/models/edge_taker.py` | new. A: MODELS 5.1, no portfolio constraint. |
| `model/models/factor_neutral.py` | new. B: MODELS 5.2, beta band, pair preference, variance budget. |
| `model/models/balanced_book.py` | new. B-simple: MODELS 5.2b, four-quadrant balance. |
| `model/models/empirical_longshot.py` | new. C: MODELS 5.3, two shapes, the SPY stop. |
| `model/run_all.py` | new. Runs the four plus the control, writes every report. `best_and_worst_days` returns `pnl_days`, not `days`: the first cut of it overwrote `metrics['days']` and a one-day run reported itself as two. |
| `model/sim.py` | changed: added `project()` so B and B-simple size on the same ladder walk the harness fills with; added `Order.reenter`, `resolved_yes_share` and the selection note (see P3 deviation 5, open question 1). |
| `model/tests/test_models.py` | new. 29 tests. |
| `model/tests/test_run_all.py` | new. 12 tests. |
| `model/reports/*.md` | new. Five per-model reports and `all_2026-09-16_2026-09-16.md`. |

Model D (`maker`) was not built; MODELS 5.4 defers it and
`test_model_d_the_maker_is_not_built` pins that.

Nothing in `data/` was modified. Nothing committed.

### Reproduce

```
.venv/bin/python -m pytest data/tests model/tests -q   # 374 passed
.venv/bin/python model/run_all.py                      # 13s, offline
```

### Measured, 2026-09-16 (21 cycles, one session, $10,000 bankroll)

| model | positions | wanted $ | filled $ | confirmed $ | resolved | PnL opt | PnL conf | worst day |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| edge_taker | 43 | $16,950 | $3,783 | $537 | 17 | $-1,064.46 | $38.92 | $-1,103.04 |
| factor_neutral | 17 | $6,109 | $587 | $334 | 2 | $4.43 | $0.00 | $14.39 |
| balanced_book | 33 | $7,510 | $1,983 | $537 | 12 | $10.20 | $38.92 | $-363.89 |
| empirical_longshot | 82 | $90,950 | $5,949 | $2,131 | 6 | $553.98 | $34.54 | $-1,181.79 |
| random_control | 59 | $51,050 | $8,000 | $3,565 | 9 | $-470.42 | $112.73 | $-1,451.79 |

**None of the PnL columns is a result.** All 51 settled markets in this window
resolved `Yes` and none reached its deadline (P3 open question 1), so every NO
lost and every YES won by construction. `empirical_longshot` "earns" $554
because 6 of its 6 resolved positions were on the YES side of a touch, and
`edge_taker` "loses" $1,064 because 10 of its 17 were NO. The control out-earns
all four models on the confirmed ledger. Read the capacity and the
constraint columns; ignore the PnL until 2026-09-30.

**Capacity, which is a result.** Fill rate as a share of dollars wanted:
`edge_taker` 22.3%, `factor_neutral` 9.6%, `balanced_book` 26.4%,
`empirical_longshot` 6.5%, control 15.7%. Median fill `depth_rank` 0 for all
five. The most any model put to work on a $10,000 bankroll was $5,949, and the
binding constraint was the book, not the caps, on every order that was not
refused by the lifetime rule.

**Confirmed share of filled dollars:** `factor_neutral` 56.8%, control 44.6%,
`empirical_longshot` 35.8%, `balanced_book` 27.1%, **`edge_taker` 14.2%**. The
ordering is the ordering of how deep into the ladder each model reaches. A is
the model with no portfolio constraint and it is the model whose fills are
least likely to have been real.

**Exposure, peak |net beta-dollar-delta| as a share of bankroll:**
`factor_neutral` 9.9%, control 329%, `empirical_longshot` 681%,
`balanced_book` 8,520%, `edge_taker` 25,715%. The gap between B and B-simple is
three orders of magnitude, which is the measurement MODELS 5.2b asked for: on
this window the Greeks are not ceremony. B-simple's quadrant balance controls
dollars paid and does nothing whatever to control delta.

**Concentration** (share of dollars deployed, not of bankroll): largest ticker
`edge_taker` 18.3% EWY, `factor_neutral` 65.7% OPEN, `balanced_book` 26.2%
OPEN, `empirical_longshot` 16.1% SPX, control 12.5% META. B's 65.7% is inside
its 8%-of-bankroll cap in absolute terms ($379 of a $800 allowance); it looks
concentrated only because B deployed $587 in total.

**`edge_taker`'s 25,715% peak is one position.** A SPY `hit_down` NO at barrier
750 with spot 752.42: 44,092 shares for $166.39 at $0.001-$0.010, carrying
$1.75M of beta-dollar-delta. Every share of it failed the persistence check.

### Deviations from MODELS.md and from the prompt

1. **The prompt names `model/sim.py` as an input; P3 had never been run**, so
   the harness, `model/factors.py` and the ledger schemas were built first in
   this same pass and written up as the P3 section above. The user chose that
   over building P4 against a thin shim (2026-09-18).

2. **The MODELS 5.1/5.2/5.2b signal lives in `sim.candidate_edges`, not in
   three model files.** 5.2 says "same signal as A" and 5.2b inherits it; three
   copies would have been three chances for them to drift apart, and the
   comparison between the models is only meaningful if the signal is identical.

3. **The conservative fair differs by side.** MODELS 5.1 writes
   `min(fair_K, fair_atm)`, which is correct for a YES, whose value is `q`. For
   a NO, whose value is `1 - q`, the cautious `q` is the *larger* of the two, so
   the model takes `max`. Using `min` on both sides would flatter every NO
   exactly on the markets where the two vols disagree most — median `spread_iv`
   is 0.158 on NFLX (P2). Pinned by
   `test_the_conservative_fair_differs_by_side`.

4. **B's beta band is an entry rule, and MODELS 5.2 asks for an invariant.**
   5.2 says "keep the portfolio's net beta-dollar-delta within +/-5% of
   bankroll **at every cycle**", then gives the only mechanism as "a candidate
   that would push it outside is skipped or trimmed". Those are not the same
   thing, and the difference is measurable: on 2026-09-16 `factor_neutral` was
   trimmed to exactly -$500.00 at 16:44Z and then drifted, with **no further
   trades**, to -$771, -$600, -$726, -$822 and -$991 by 18:45Z. That is 2.0x
   its own band, on a day SPY moved -0.44%. The drift is EWY (beta 2.80) and
   OPEN (beta 2.93) `hit_down` YES positions whose deltas steepened as spot
   approached their barriers. Restoring the band would require selling, and
   MODELS 2 rules out selling before resolution at 18-48% round trip. **So the
   band as specified is not attainable in a hold-to-resolution book, and what B
   actually implements is a band at entry.** Reported rather than papered over;
   see open question 1.

5. **The variance budget uses dollars paid as `notional`, as 5.2 writes it.**
   `q(1-q) * notional^2` with notional = max loss. The truer scale is the
   payoff swing, which is *shares*: a $150 YES at 30c is 500 shares, and on
   that reading a single position (std $229) exceeds the whole $200 budget. On
   the dollars-paid reading the budget allows about six average positions
   before the 0.5 same-direction correlation closes it. The spec's reading is
   the loose one and is what is implemented; it still fired 2 refusals in B's
   last cycle and it is why B holds 17 positions where A holds 43.

6. **B's pair preference is evaluated before singles and all-or-nothing.**
   MODELS 5.2 says to take both legs when both clear the bar; taking them one
   at a time would let the beta band refuse the first leg and never see the
   second, which is the one case where the band refuses a position that would
   have *reduced* risk. Pinned by
   `test_b_accepts_the_same_position_once_an_opposite_one_exists`.

7. **`balanced_book` keeps the per-direction cap at 100%.** MODELS 5.2b names
   only the per-ticker cap. The quadrant rule is a strictly stronger statement
   about the same axis — two quadrants make a direction — and running both
   would leave the report unable to say which one bound.

8. **Model C does not exclude `deep_otm`.** MODELS 3.4 forbids trading those
   *on the option signal*; C's signal is five years of realised highs and lows
   and the far barriers are exactly what its longshot shape is about. The flag
   is carried into the ledger so the effect can be read off later. A, B and
   B-simple do exclude them.

9. **C treats a missing `fair_K` as no veto.** MODELS 5.3 says the engine "does
   not put `fair_K` above the YES ask"; a fifth of the universe is commodity
   and FX ladders with no equity chain at all (P2), and reading "no opinion" as
   "disagrees" would delete the part of the universe this model exists to
   trade.

10. **C's SPY stop latches for the day.** MODELS 5.3 says "for the rest of the
    day"; a stop that lifts when the tape bounces is not a stop, and the bounce
    is when the next leg down starts. Pinned by
    `test_the_spy_stop_does_not_un_fire_when_the_tape_bounces`.

11. **The control's activity is set from A's.** `random_control` proposes the
    mean orders per cycle that `edge_taker` proposed (8 here). A control that
    trades ten times less than the model is a control for a different
    experiment.

### Dependencies

**None added.**

### Open questions

1. **What should B do when the market walks it out of its band?** Deviation 4
   measures the problem: entry-only control gave 2x the band within three hours
   on a quiet day. The options are (a) accept it and rename the constraint an
   entry band, (b) let B hedge with the underlying rather than by selling the
   position, which is outside everything built so far, or (c) size entries to a
   fraction of the band so drift has somewhere to go. This is a decision about
   MODELS 5.2, not a bug to fix in `factor_neutral.py`.

2. **Is `balanced_book`'s 8,520% peak exposure a refutation of 5.2b or of the
   window?** The quadrant rule controls dollars paid and, on one session, did
   nothing to control delta. That is the comparison 5.2b was built for and the
   answer looks decisive — but it rests on one day, and the quadrants were
   nowhere near balanced because the slack ($500) was the binding allowance for
   most of the session.

3. **`empirical_longshot` wanted $90,950 and filled $5,949 (6.5%).** It
   proposes far more than any other model because the longshot shape fires on
   every cheap NO in the universe, and then 330 orders die on the 30%
   per-direction cap. Either the shape is too loose or the cap is doing the
   model's selection for it; the ledger has the fills to tell which.

4. **Every model's median fill is at `depth_rank` 0 and no model deployed more
   than 60% of bankroll.** Capacity, not edge, is the binding constraint at
   $10,000 — which is the question FINDINGS asked and which this window answers
   the same way for all four models. Whether that survives a month of data is
   the thing the simulation is for.

---

## P5 2026-09-18 — daily report

### Files

| path | what it is |
|---|---|
| `model/daily.py` | new. One command: price the unpriced session cycles, re-run every model over the whole window, write `model/reports/daily_<date>.md`, and read the collector's gaps out of the `runs` table. |
| `model/data.py` | changed: added `runs(since, jobs)`, which parses `counts_json` for `tokens_requested`, `tokens_returned`, `missing` and `levels` and tags each cycle `in_session`. |
| `model/run_all.py` | changed: `main` split into `run_everything(dates, control, echo)`, so `daily.py` and `run_all.py` cannot silently be two different experiments. Output verified identical before and after. |
| `scheduling/com.polyoptions.report.plist` | new. 18:00 America/New_York, runs `model/daily.py`, logs to `data/logs/launchd-report.{out,err}`. |
| `scheduling/install.sh` | changed: `com.polyoptions.report` added to `LABELS`; the ET→local conversion now emits four numbers (17:30 and 18:00) instead of two; two `__REPORT_*__` placeholders added to the `sed` list; the summary echoes the report job's local time and the reports directory. |
| `model/tests/test_daily.py` | new. 26 tests. |
| `model/reports/daily_2026-09-16.md` | new. The report, 64 lines. |

Nothing in `data/src/` was modified. Nothing committed. **`install.sh` was not
executed**: running it loads launchd agents, which is a change to the machine
rather than to the repository, and the prompt did not ask for the job to be
installed. Everything it would do was verified without it (see Measured).

### Reproduce

```
.venv/bin/python -m pytest data/tests model/tests -q   # 400 passed
.venv/bin/python model/daily.py                        # 13s, offline
./scheduling/install.sh                                # installs the 18:00 ET job
```

`model/daily.py --date <date>` reports on a chosen date; `--no-price` skips
step 1.

### Measured, 2026-09-18

**Runtime 13 s** end to end, of which ~13 s is the five model runs; step 1
priced 0 cycles because everything with books and a session is already in
`model/store/fair`. A cold cycle costs ~6 s to price, so a normal evening after
a full session is 21 cycles × 6 s + 13 s ≈ 2.5 minutes.

**Step 1 was verified by breaking it.** One part file — the one holding
`20260916T195321Z-6625e2` — was removed from the fair store against a backup.
`unpriced_cycles` then named exactly that cycle and nothing else,
`price_missing` repriced it in 6.4 s, and the store returned to 13,980 rows
across 21 cycles. The repriced values match the originals to a **maximum
`fair_K` difference of 9.2e-7 across 651 markets** — see open question 2.

**The report is 64 lines**, against the prompt's 120. Pinned at the ceiling by
`test_the_report_stays_under_a_hundred_and_twenty_lines`, which builds five
models with eight settlements each and eight days of collector rows.

**The collector section earns its place on its first run.** Over the last seven
days:

| date | in-session cycles | runs | median gap | longest gap | over cadence | not ok | failures | tokens missing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 21 | 28 | 17.5 min | 41.2 min | 2 | 4 | 10 | 1,282 |
| 2026-09-17 | 0 | 8 | — | — | 4 | 4 | 10,123 | 10,473 |
| 2026-09-18 | 0 | 3 | — | — | 1 | 0 | 26 | 0 |

2026-09-17 is the sleeping laptop, and it is legible without knowing that in
advance: zero in-session cycles, four runs over cadence, 10,123 failures and
10,473 tokens never returned. 2026-09-16 — the day every model is measured on
— is not clean either: **a 41-minute gap against a 17.5-minute median, two runs
over cadence and 1,282 tokens missing.** The nominal cadence is 15 minutes and
the day's actual median is 17.5, which is why gaps are measured against the
date's own median rather than a fixed grid.

**launchd wiring, verified without installing.** The ET→local conversion emits
`5 30 6 0` on this machine (UTC+8): 17:30 ET → 05:30 local, 18:00 ET → 06:00
local. All three plists were substituted with those values into the scratchpad
and `plutil -lint` returned OK for each, with no `__PLACEHOLDER__` left in any
of them. `test_every_placeholder_in_every_plist_is_substituted_by_install_sh`
pins that: a placeholder a plist uses and `install.sh` does not know about
would install a job that never fires.

### Deviations from MODELS.md and from the prompt

1. **One reliability table with a `model` column, not one table per model.**
   The prompt asks for "per model ... the reliability table so far" and for
   under 120 lines; five separate tables with their own headers cost ~25 lines
   of chrome before any data. Each model's rows are contiguous, so it reads as
   five tables and occupies one.

2. **The report date falls back.** The job fires at 18:00 ET, so the default is
   today in New York — but a weekend, a holiday or a missed session has no
   priced cycle, and an empty report is worse than a late one. `resolve_date`
   falls back to the newest priced date and the fallback is stated in the
   report's own header, not only on stderr. Today's run reports 2026-09-16 and
   says so: 2026-09-18 has books but no cycle inside 13:30–20:00Z.

3. **Gaps are measured against each date's own median interval**, not against
   the nominal 15-minute cadence. A snapshot that takes three minutes shifts
   every later tick, and a fixed grid would report that as a gap every day.
   2026-09-16's median is 17.5 minutes for exactly that reason. "Over cadence"
   is still measured against the nominal 900 s, because a run longer than one
   tick has overlapped the next one however the ticks are spaced.

4. **`price_missing` appends; `run_fair.py` still clears the partition.** They
   look inconsistent and are not: `run_fair.py` reprices a whole date and would
   double every market-cycle without clearing, while `price_missing` writes only
   cycles that were absent. Pinned by
   `test_price_missing_writes_one_partition_entry_per_missing_cycle`.

5. **An empty pricing result is not written.** A cycle whose markets all failed
   to price produces no rows, and writing an empty part file would leave a
   partition entry claiming the cycle is done — so the next run would not retry
   it.

6. **The selection warning from P4 is repeated at the top of the daily report.**
   It is the same caveat, and a daily artifact that a reader opens without
   having read `all_*.md` needs it more, not less.

7. **The report job does not wait for `com.polyoptions.daily`.** That job has
   run for over four hours on a backfill (16,076 s on 2026-09-17) and 18:00 ET
   is thirty minutes after its 17:30 start. The report reads books the snapshot
   job already stored, not the bars the daily job is fetching, so an overlap
   costs it the newest daily bars and nothing else. Stated in the plist.

### Open questions

1. **2026-09-16 is not a clean session and every number in P3 and P4 rests on
   it.** 41-minute gap, two runs over cadence, 1,282 tokens never returned.
   Nothing here is wrong, but "21 priced cycles" reads as a full day and is
   not one. Whether the missing tokens are concentrated in particular markets —
   which would bias the universe rather than thin it — is answerable from
   `runs.counts_json` against the books actually stored, and is not yet done.

2. **Re-pricing a cycle is reproducible to ~1e-6, not bit-identical.** Maximum
   `fair_K` difference 9.2e-7 over 651 markets. That is three orders of
   magnitude below the Monte Carlo standard error P2 measured (0.0035) and four
   below the 3c decision threshold, so it changes nothing today. It does mean
   P2's `test_pricing_is_reproducible` holds within a run but not across
   processes; the likely cause is float summation order in the smile build,
   which depends on DuckDB's row order.

3. **`daily.py` re-runs every model over the whole window every evening.** At
   13 s for one date that is free; at two months it is 40+ dates of replay each
   night. The ledger is already written per run, so an incremental mode that
   replays only the new date and folds it into the stored ledger is possible —
   but it would have to handle a resolution arriving for a position opened
   weeks earlier, which the current full replay gets right for nothing.

4. **Nothing warns when the report is stale.** If the collector stops, the
   fallback in deviation 2 keeps producing a readable report about an older
   date, forever. The collector table shows it, but a reader has to look. A
   threshold — "today is more than N sessions after the newest priced date" —
   would belong in the header next to the fallback note.

---

## P6 review 2026-09-18

Review of everything under `model/`, against the seven defect classes the
prompt names. **Six findings, five fixed, two left open** (one finding has both
a fixed and a left-open half). Nothing was rewritten that was not broken: the
fixes are four files and about sixty lines.

### Findings

#### 1. A daily bar for today is visible during today's session — FIXED

`model/engine.py:89` and `model/factors.py:43` both filtered daily bars with
`bar_time < now`. A daily bar for session D is stamped at **Eastern midnight**
(04:00Z in EDT, 05:00Z in EST — 830 and 425 of SPY's 1,255 stored bars), which
is *before* the session it summarises. So at a 14:00Z cycle on 2026-09-16 the
filter passed the bar carrying that day's 16:00 ET close.

Concrete input: `TickerContext("PLTR", 2026-09-16T14:00Z)`.
- `sigma_realised` **0.69202 with today's bar, 0.70345 without** — 1.1 vol
  points. EWY 0.61539 / 0.62594, NVDA 0.40272 / 0.40905, SPY 0.11104 / 0.11221.
- `factors.beta` moved by up to 0.017 (OPEN 2.9318 / 2.9150).
- `empirical.touch_table` received today's high and low.

Everything model C trades on descends from `sigma_realised` and that table.

**Fix:** `data.daily_bars_asof(symbol, as_of)`, which keeps bars whose session
had closed at `as_of`, cutting at 16:00 **Eastern** on the bar's own date
through the tz database. `engine.TickerContext` and `factors._returns` both go
through it.

A first attempt used `sessions.deadline_close` for the cut and was caught by
its own test: `nyse_sessions` defaults to 2025-12-01..2026-12-31 and the bars
go back five years, so every bar before 2025-12 returned NaT and was dropped —
four of five years of history, silently. The ET rule needs no calendar. A half
day (13:00 ET close) is held back three hours longer than necessary, which
costs nothing because no cycle in the replay is after 20:00Z.

**Tests:** `model/tests/test_lookahead.py`, 9 tests, including
`test_the_store_stamps_a_daily_bar_at_eastern_midnight` (the convention the cut
depends on) and `test_the_realised_vol_a_cycle_sees_does_not_know_todays_close`
(a 10% crash today must not move the volatility a 14:00Z cycle prices on).

#### 2. `f` was estimated from the whole stored sample — FIXED

`model/empirical.py:102`. `estimate_intraday_share(ticker)` read every daily
bar with no as-of, so `f` — which enters **every** option fair value — was
computed on day one from data collected in week eight.

Size on today's window: **≤ 0.0008** (EWY 0.3275 full sample against 0.3266
as-of; SPY and NVDA agree to 3e-5), because it is a five-year variance ratio
and one bar barely moves it. Fixed because it is free to fix and grows with the
window, not because it mattered today.

**Fix:** `estimate_intraday_share(ticker, default, as_of=...)`; the engine
passes the cycle's reference time.

#### 3. Duplicated daily bar times reached every consumer — FIXED

**All 19 equity tickers** carry a duplicated daily bar stamp (AAPL and TSLA
carry two): P1's backfill appended hourly bars beside the collector's daily
ones and `select distinct` keeps both rows when the closes differ in the last
decimal. A duplicate is a spurious `ln(C/C) = 0` return in the EWMA — which is
part of why finding 1's "with today" volatilities read *low* — and a spurious
same-day "gap" in `f`.

P3 had patched this inside `model/factors.py` only; the engine still saw
duplicates. **Fix:** the dedupe moved into `data.daily_bars`, which is the one
door every consumer passes through, and `factors`' local copy was removed.
`test_duplicate_bar_times_do_not_break_the_join` was re-pointed at the real
reader rather than deleted — patching `daily_bars` would have patched out the
thing under test.

#### 4. A position recorded the engine's fair, not the one it traded on — FIXED

`model/sim.py`, `execute`. `Position.entry_fair` was set from `meta["fair_K"]`
for every model. MODELS 4.5 scores "mean fair of positions vs hit rate", so
`empirical_longshot` — whose signal is the empirical frequency and which uses
the option engine **as a veto only** (MODELS 5.3) — had its reliability table
scored against the number it was refusing to trade on.

Concrete input: the six `empirical_longshot` positions that resolved recorded
0.3196 / 0.4139 / 0.3965 / 0.3889 / 0.5617 / 0.5643 against the 0.3689 / 0.5590
/ 0.6457 / 0.5501 / 0.5429 / 0.5284 the model actually traded on — up to 0.25
out. `edge_taker` was affected too: it screens on `min(fair_K, fair_atm)` and
recorded `fair_K`.

**Fix:** `Position.entry_fair` is now `Order.fair`, with `Position.fair_source`
beside it; `Position.entry_fair_engine` carries `fair_K` for MODELS 4.2's 10c
re-entry test, which the harness must be able to evaluate at a later cycle and
cannot do with a model's private signal. `sim.model_fair(row, source)` marks an
open position on the same fair it was taken on, so MODELS 4.3's "at fair (model
mark)" is the model's mark. Marking is lenient where screening is strict: a
position whose ATM vol went missing this cycle is better marked on the strike
vol than dropped from the book's value. `entry_fair_engine` and `fair_source`
were added to the `resolutions` ledger schema.

The random control has no forecast, so its `entry_fair` is NaN and it now gets
no reliability rows — correct, and the daily report names it rather than
dropping the row silently.

#### 5. `np.fmin` made a half-priced market look fully priced — FIXED

`model/sim.py`, `candidate_edges`. MODELS 5.1's bar is `min(fair_K, fair_atm)`,
which is undefined when one is missing — but `np.fmin(0.40, nan)` returns 0.40,
so such a market would have screened as though both vols agreed with it.

**Zero rows in the 2026-09-16 store trigger it** (`deep_otm` returns True when
`sigma_atm` is not finite, which filters them first), so this is a latent
defect, not a measured one. Fixed anyway: it is one line, and the guard that
currently hides it is in a different module for a different reason.

#### 6. Rates printed without the base they are a share of — FIXED

- `sim.report_markdown`: "largest single position 13.3% of dollars" against a
  total the report never printed. Now headed *"Shares of $3,756.63 deployed
  across 43 positions"*.
- `daily.report`: a `% bankroll` column with the bankroll stated nowhere. Now
  states it.
- `run_all.combined_report`: same for `|beta$delta| max`, and it now says that
  `top ticker` is a share of that row's `filled $`.

Everything else already carried its count: the reliability tables, the Brier
table, the capacity tables, the flag table in `run_fair.py`.

#### 7. The five-minute bar that has not finished forming — HALF FIXED

A 5-minute bar stamped 13:40Z covers 13:40:00–13:44:59, so its close is up to
five minutes ahead of a 13:40Z cycle. SPY's open-to-close move inside a
5-minute bar on 2026-09-16: **median 0.031%, p90 0.153%, max 0.323%** across 78
bars — the maximum is the whole distance to a barrier 0.3% away.

- **`factors.spy_intraday_return` — FIXED.** Model C's −1.5% stop now reads
  only bars whose five minutes are up. A stop that fires on a move which has
  not finished happening is not a stop, and nothing in MODELS specifies this
  one's data source. Two P3 tests encoded the old semantics and were updated;
  their intent was unchanged.
- **`data.spot_at` / the engine's spot — LEFT OPEN.** MODELS 3.1 specifies it
  in as many words: "close of the 5-minute bar *containing*
  `book_timestamp_utc`". It is lookahead of up to five minutes and the number
  above is its size. Changing it is a change to MODELS 3.1, not a bug fix, so
  it is reported and left. No behaviour changed on 2026-09-16 either way: SPY's
  worst intraday move that day was −0.44% and the stop never fired.

#### 8. `earnings_inside` is computed from data fetched after the session — LEFT OPEN

`data.upcoming_earnings()` has no as-of. Worse than it looks: the earliest
`fetched_at_utc` in the `earnings` table is **2026-09-16T21:44Z**, after every
replayed cycle of that day, so there is no as-of-correct value for this window
at all — a correct filter would make the flag uniformly False and delete the
2.3% P2 reported.

Left open deliberately: **no model reads the flag**, so nothing trades on it,
and turning a reported 2.3% into a reported 0.0% is a decision about what the
flag means rather than a fix. It needs deciding before any model uses it.

### Checked and found correct

- **Fills walk the right token.** `state.ladder` and `check_persistence` both
  key on `(market_id, outcome_index)`; the NO ask is never derived from the YES
  ask. Pinned by `test_the_persistence_check_keys_on_the_token_not_the_market`.
- **Fees are charged per share on the price paid**, in `sim.fee_per_share` and
  in `calibration.cost_to_buy` alike — `0.04 * min(p, 1-p)`, never on notional.
- **No average of `(bid - fair)` or `(fair - ask)` anywhere.** Every ROI in the
  reports is an aggregate `pnl / cost`, and the reliability tables average
  probabilities, not edges.
- **Closed and already-touched markets.** `touch_pending` is set and filtered;
  `T_sessions <= 0` yields no fair at all and is filtered; **0 rows** in the
  store carry a deadline before their own book time.
- **The regular-hours correction is applied once.** `touch_probability` splits
  the path into an unobserved gap of `(1-f)` variance and an observed session of
  `f`; `fair_cont` deliberately omits it as the upper bound; `fair_empirical`
  gets it from the data, since a yfinance daily high/low *is* the regular
  session, and both the table and its lookup use full close-to-close volatility,
  so it is applied in one place on each path.
- **The persistence check is never used for a decision.** The portfolio runs on
  the optimistic ledger; `confirmed` reaches only the reports.
- **`deep_otm` returns True when `sigma_atm` is missing**, which is the
  conservative direction.
- **`resolutions_upto` filters on `settled_at_utc`**, the economically correct
  clock, not on when the collector happened to notice.

### Did the numbers change? Yes.

`model/store/fair/date=2026-09-16` was repriced (126 s) because findings 1–3
change the engine's inputs, then `run_all.py` was re-run.

| model | filled $ | resolved | PnL opt | PnL conf | open @fair | positions |
|---|---|---|---|---|---|---|
| edge_taker | 3,783 → **3,757** | 17 → 17 | −1,064.46 → **−1,088.46** | 38.92 → 38.92 | 105.55 → **87.78** | 43 → 43 |
| factor_neutral | 587 → **591** | 2 → 2 | 4.43 → **4.42** | 0.00 → 0.00 | 22.31 → **17.94** | 17 → **18** |
| balanced_book | 1,983 → **1,957** | 12 → 12 | 10.20 → **−13.80** | 38.92 → 38.92 | 128.21 → **114.94** | 33 → 33 |
| empirical_longshot | 5,949 → **6,000** | 6 → **4** | 553.98 → **246.48** | 34.54 → **41.89** | −1,191.08 → **+271.79** | 82 → **74** |
| random_control | 8,000 → 8,000 | 9 → 9 | −470.42 → −470.42 | 112.73 → 112.73 | −446.28 → −446.23 | 59 → 59 |

The control is unchanged to the cent, which is the check that the differences
are the signal moving and not the harness: it picks markets at random and reads
no fair value, so nothing in findings 1–5 can touch it.

**`empirical_longshot` moved most, as it had to.** Its entire signal is
`fair_empirical`, which is built from `sigma_realised` and the touch table —
both of which stopped seeing today's bar. Higher realised volatility means a
smaller `k`, which means higher modelled touch frequencies, which means fewer
markets pass `empirical <= half the YES price`: 82 positions became 74, of
which 4 rather than 6 have resolved. Its `open @fair` swing of **+$1,463** is
finding 4: the book is now marked on the empirical fair it trades on instead of
the option fair it explicitly refuses, and a longshot NO the engine prices at
0.90 and the model prices at 0.98 is worth more on the model's own mark.

`balanced_book` crossing from +$10.20 to −$13.80 is a $24 move on a
selection-biased sample of 12 resolved positions; it is noise and should not be
read as a result. Everything in the "not yet a result" warning of P4 and P5
still stands — the deadlines have still not passed.

### Tests

**423 passed** (400 before), all under `data/tests` and `model/tests`. Added:
`model/tests/test_lookahead.py` (9, new file), 7 in `test_sim.py`, 3 in
`test_factors.py`, 3 in `test_models.py`, 2 in `test_daily.py`. Three existing
tests were updated where a fix changed the semantics they pinned, none deleted:
the two intraday-move tests in `test_factors.py`, the dedupe test re-pointed at
its new seam, and the re-entry test moved to `entry_fair_engine`.

### Open questions

1. **MODELS 3.1's containing-bar spot** (finding 7). Worth up to 0.32% of spot
   on a 5-minute bar, against barriers that sit 0.3% away. Changing it needs a
   decision about the spec.
2. **`earnings_inside`** (finding 8) has no honest value for this window.
3. **Finding 1 was a whole class, not an instance.** The pattern — a timestamp
   that names a period rather than an instant — is what made it invisible, and
   the same shape exists wherever a period is keyed by its start. `end_date` on
   `poly_markets` is already handled (P2 deviation 9); the 1-hour bars P1
   backfilled have not been audited for it.
4. **The repricing is reproducible to ~1e-6, not bit-identical** (P5 open
   question 2, unchanged). That remains unexplained and below every threshold
   that matters.

---

## Review (Fable) 2026-09-18

Assessment of P1–P6 against the code, the store and the reports. Everything
P6 checked was re-checked; the two defect classes below are ones P6 either
signed off as correct or left open as a spec question. **Three findings fixed,
one handoff error corrected, one left open.** Every test passes (430, from
423); the fair store for 2026-09-16, every model report, the calibration
report and the daily report were regenerated.

### Findings

#### 1. A touch is a path, not a level — FIXED (P6 "checked and found correct" was wrong)

`model/engine.py` flagged `touch_pending` only when the *current* spot was
beyond the barrier. The rules text (`poly_markets.rules_text`, read today)
counts every regular-hours 1-minute candle "during the week of March 30" or
"during May", so a barrier that printed at 10:00 is a settled YES at 15:00
however far the spot has bounced — and the book knows it: the NO is offered at
1c. The engine, seeing spot back above the barrier, priced that NO at 0.79.

Concrete input: SPY `hit_down 750`, 2026-09-16. Low 749.60 at 19:25Z, spot
752.42 at 19:36Z, `edge_taker` bought 44,092 NO shares at $0.001–$0.010. Same
shape on COIN 162.5 / 167.5, EWY 174 / 176, MSTR 125, all bought after the
print. Measured on the P6 ledger against finished regular-hours 5-minute bars
from the period start:

| model | NO fills on an already-touched market | $ | of resolved NO positions |
|---|---:|---:|---|
| edge_taker | 7 positions | $687 | 7 of 10, −$655 of its −$1,088 |
| balanced_book | 3 | $135 | 3 of 5 |
| factor_neutral | 1 | $19 | 0 resolved |
| empirical_longshot, control | 0 | — | — |

**Fix:** `sessions.period_start` (Monday 00:00 ET of the deadline's week, or the
1st of its month), `data.session_bars_5m` (regular hours, in Eastern — the
commodity ladders carry 39,693 extended-hours bars, the equities none) and
`data.touched_between`, which only reads bars whose five minutes are up. The
engine sets `touch_pending` from it. `touch_pending` went from **0.3% to
11.3%** of market-cycles; priced market-cycles 13,980 → 11,462. The new ledger
has **0** fills on an already-touched market (re-measured).

**Left as is:** the 5-minute bars start 2026-06-22, so a touch before that date
is invisible to the check; no replayable cycle is affected.

#### 2. The spot was a print from the future — FIXED (P6 finding 7, left open as a spec question; MODELS 3.1 changed)

`data.spot_at` returned the *close* of the 5-minute bar containing the book
time, as MODELS 3.1 said. A bar stamped 15:50Z closes at 15:54:59, so a
15:51Z cycle priced off a print up to four minutes ahead — P6 measured the
within-bar move at p90 0.15%, max 0.32%, against barriers 0.3% away. It is now
the bar's **open**, the last print a trader could have seen. MODELS 3.1 is
amended in place with the reason; this is the decision P6 asked for.

The same function now takes `fetched_at_utc`, not `book_timestamp_utc`, for
both the spot and `T_sessions`. The latter is the venue's last change to the
book (up to 4.9 days old, P2); a resting order is executable at the moment it
was seen, so that is the moment its fair value is for. On the P6 ledger only
the control had fills on a book older than 15 minutes (3 fills, $58), so this
is a correctness fix that moved nothing today.

#### 3. P1's hourly spot was the bar's close: lookahead in the +13.1% — FIXED

`run_calibration.build_points` joined each hourly price point to the hourly
bar at or before it and took its **close**. yfinance stamps an hourly bar at
its start (13:30Z covers 13:30–14:30Z, verified in the store), so a 14:00Z
point was priced off a 14:30Z print. That favours exactly the rule that won:
"buy YES when empirical − price ≥ 5c" fires when the spot has moved toward the
barrier while the quote has not, and with the close it fires on moves that
had not happened yet. Now the containing bar's open (`run_calibration.hourly_spot`).

| | before | after |
|---|---:|---:|
| buy YES when empirical − price ≥ 5c | +13.1% over 4,780 bets | **+10.3% over 4,688 bets** |
| price-matched control for it | −24.1% (sd 1.1%) | −24.5% (sd 1.4%) |
| buy NO when YES ≤ 10c | −3.2% over 4,708 | −3.2% over 4,708 (no spot in it) |
| Brier: empirical / continuous / market | 0.1028 / 0.1045 / 0.1090 | 0.1030 / 0.1047 / 0.1091 |
| by month (YES rule) | Apr +34.4, Jun +17.4, Jul +18.5, Aug −1.4 | Apr +31.2, Jun +13.8, Jul +15.9, Aug −2.0 |

The reliability tables (sections 2 and 3) are byte-identical before and
after: they never used spot. **The P1 conclusion survives** — the empirical
signal still separates from its control by 35 points — but three of its
thirteen points were lookahead, and P1's open question 2 (April/June/July
concentration) is unchanged.

#### 4. P1's handoff quoted Brier numbers that were never in the report — CORRECTED

HANDOFF P1 said 0.1083 / 0.1104 / 0.1229 / 0.1676; the report on disk said
0.1028 / 0.1045 / 0.1090 / 0.1583 before today's change. The handoff numbers
predate the placeholder exclusion (P1 deviation 6) and were not updated. Fixed
in place, marked.

### Checked and found correct

- **P2's IV, smile, two-clock and expiry handling**; the monitored-vs-continuous
  numbers; `f` per ticker. Not re-derived, but the tests that pin them ran
  against the repriced store and the IV check reproduces (median gap 0.0019,
  8,060 of 61,002 past 0.01, identical to P2).
- **P3's fills walk the right token, fee on price paid, persistence keyed on
  `(market_id, outcome_index)`, no-lookahead on bars and resolutions,
  `max(fetched_at_utc)` as the cycle clock**, and the settlement sweep.
- **P4's four models** implement 5.1/5.2/5.2b/5.3 as their deviations describe.
  The side-dependent conservative fair (P4 deviation 3) is right.
- **P5's plist and `install.sh` edits** are consistent; `install.sh` was not run
  here either.
- **P6 findings 1–6** hold and were not disturbed.

### Did the numbers change? Yes, and the direction is the one the findings predict

`run_fair.py --date 2026-09-16` (125 s), then `run_all.py` and `daily.py`:

| model | positions | filled $ | resolved | PnL opt | PnL conf | \|beta$delta\| max |
|---|---|---|---|---|---|---|
| edge_taker | 43 → **49** | 3,757 → **4,480** | 17 → **11** | −1,088 → **−603** | 38.92 → 26.60 | 25,768% → **2,302%** |
| factor_neutral | 18 → 21 | 591 → 1,026 | 2 → 1 | 4.42 → −9.10 | 0.00 → 0.00 | 9% → 8% |
| balanced_book | 33 → 18 | 1,957 → 1,567 | 12 → 4 | −13.80 → −414.25 | 38.92 → 20.98 | 8,530% → 1,008% |
| empirical_longshot | 74 → 54 | 6,000 → 6,000 | 4 → 2 | 246 → 29 | 41.89 → 17.37 | 1,112% → 1,786% |
| random_control | 59 → 55 | 8,000 → 8,000 | 9 → 6 | −470 → −136 | 112.73 → 2.67 | 326% → 364% |

`edge_taker`'s 25,768% peak — the one SPY position — is gone because the
market it sat in is now `touch_pending`. The control moved because it draws
from the tradeable universe, which shrank by 11%. `balanced_book` lost 15
positions: with the already-touched markets out, the quadrant slack binds on
different orders. **None of the PnL columns is a result**, for P3 open question
1's reason, and that has not changed: 2026-09-30 is still the first date with an
unbiased sample.

### Tests

430 passed (423 before). Added: 5 in `test_lookahead.py` (the open-not-close
spot, the path touch, the forming bar, extended hours and the period start), 1
in `test_run_calibration.py` (hourly spot alignment), 1 integration test in
`test_engine.py` asserting no priced market in the 15:51Z cycle had its
barrier printed earlier in the period. No test was deleted or weakened.

### Files

| path | change |
|---|---|
| `model/data.py` | `spot_at` returns the containing bar's open; new `session_bars_5m`, `touched_between`. |
| `model/sessions.py` | new `period_start`. |
| `model/engine.py` | path-based `touch_pending`; spot, horizon, quote age and earnings window on `fetched_at_utc`. |
| `model/run_calibration.py` | new `hourly_spot` (bar open); the two paper-PnL figures in the prose are computed, not typed. |
| `model/MODELS.md` | 3.1 amended, marked and dated. |
| `model/tests/test_lookahead.py`, `test_run_calibration.py`, `test_engine.py` | tests above. |
| `model/store/fair/date=2026-09-16`, `model/store/ledger/*`, `model/reports/*` | regenerated. |

No dependency added. `data/` untouched. Nothing committed.

### Second pass, same day: the two items left open above, closed

**Touch check before 2026-06-22.** `data.touched_between` now reads three bar
series and any regular-hours print counts: 5-minute (from 2026-06-22), 1-hour
(P1's backfill, from 2023-10-19, verified 0 bars outside regular hours) and
daily (five years; a daily high/low is the regular session), each admitted
only once its period is over (`+5 min`, `+1 h`, 16:00 ET on the bar's date).
On the replay window the coarser series add 42 market-cycles of
`touch_pending` (11.3% → 11.6%): prints the 5-minute series missed where it
has gaps. `edge_taker`, `factor_neutral` and `balanced_book` are unchanged to
the cent; `empirical_longshot` and the control moved by a few positions.

**`earnings_inside` (P6 finding 8), decided and extended.** Three facts, all
measured on the store:

1. Every earnings stamp is `20:00Z`, the bell, whether the company reports
   before the open or after the close. So the intervals are half-open: a
   report stamped at the deadline's own close is *after* the last candle that
   could print the barrier.
2. With that, `earnings_inside` on 2026-09-16 is **0**, not 2.3%. The 2.3% was
   MU's monthly ladder alone (315 market-cycles), and MU reports on
   30 September after the close — the deadline's close. The flag had the
   right ticker and the wrong side of the bell.
3. The as-of question is answered with a fallback, not a strict filter: dates
   first fetched at or before the cycle count; when there are none (the first
   fetch is 21:44Z on 09-16, after every cycle), the earliest fetch is used.
   A confirmed earnings date is public weeks ahead, and every date in the
   store is at least two weeks past its first fetch, so this is a documented
   approximation and not lookahead in any sense that changes a number.

The user's suggestion (2026-09-18): flag earnings between the barrier's
deadline and the option expiry the vol comes from. That is the case that
actually biases the fair: the after-expiry's variance contains a jump the
barrier never sees, and interpolating toward it lends the barrier part of the
jump. Built as **`earnings_gap`** (MODELS 3.4, new flag, new schema column):

- when a pre-earnings expiry exists, the engine carries *its* vol flat to the
  deadline (`fair.flat_vol`, `vol_source='pre_earnings_flat'`) instead of
  interpolating toward the post-earnings one;
- when none exists the fair is flagged and `sim.candidate_edges` drops it
  from the option signal, as with `deep_otm`.

On 2026-09-16 it fires on 273 market-cycles (2.0%), all MU monthly, all
repriced off the 25 September expiry. **The size of the correction today is
small**: MU's `sigma_K` is 0.660 flat off 25 Sep against 0.662 interpolated
toward 2 Oct, i.e. the 2 Oct weekly carried no visible earnings premium over
the 25 Sep one on that day. The mechanism is right and cheap; the number it
moved is not, this week.

**Threshold sweeps.** `run_all.py --min-ev 0.10 --min-roi 0.20 --tag ev10`
re-runs A, B and B-simple at a different MODELS 5.1 bar and writes reports
and ledgers under their own names. Nothing needs storing ahead of time: the
replay reads books, fair values and resolutions from the store, so any
threshold is a 15-second run at any later date. C is not re-thresholded (its
shapes are its own parameters).

| model | after first pass | after second pass |
|---|---|---|
| edge_taker | 49 pos, $4,480, PnL opt −602.56 | unchanged |
| factor_neutral | 21 pos, $1,026, −9.10 | unchanged |
| balanced_book | 18 pos, $1,567, −414.25 | unchanged |
| empirical_longshot | 54 pos, $6,000, +28.89 | 53 pos, $6,000, +182.03 |
| random_control | 55 pos, $8,000, −136.28 | 64 pos, $8,000, −562.25 |

Tests: **435 passed** (430 after the first pass). Added: the multi-series
touch fallback and the half-open earnings intervals with as-of
(`test_lookahead.py`), `flat_vol` (`test_fair.py`), the MU gap on the real
cycle and `earnings_inside == 0` (`test_engine.py`), the tagged sweep
(`test_run_all.py`). Files: `model/data.py`, `model/fair.py`,
`model/engine.py`, `model/fair_schema.py` (column added; the one partition
was repriced), `model/run_fair.py`, `model/sim.py`, `model/run_all.py`,
`model/MODELS.md` 3.4. The fair store, ledgers and reports were regenerated.

### Open questions

1. **`edge_taker` still resolved 6 NO positions for −$709 on markets that were
   not touched at entry** and touched later the same day. That is the risk the
   model carries on purpose, but with 11 resolved positions all on one session
   it is also the whole of its PnL. Nothing to fix; do not read it.
2. **P1's YES rule is +10.3% at mid + 3.5c with no book.** P1 open question 1
   stands and is now the only thing between this number and a decision.
3. **`single_expiry` (16.4%) assigns the one expiry's whole variance to the
   deadline's horizon** (`fair.interp_vol`): overstated when that expiry is
   after the deadline, understated when before. P2 wrote it that way; it is
   the same shape of problem as the earnings gap and `flat_vol` is the
   obvious replacement. Not changed, because it moves a sixth of the universe
   and deserves its own measurement against the P1 outcomes.

---

## Behaviour checks 2026-09-18

The user's request: a way to check, a few days from now, that the pipeline is
*behaving* correctly on the data it has gathered, before there are outcomes to
judge it by - so that anything wrong is fixed before the end-of-month
evaluation rather than found inside it.

### How to run

```
.venv/bin/python -m pytest model/tests/test_behaviour.py -v -rs
```

`model/tests/test_behaviour.py`, 26 checks, ~10 s. Every check reads what is
on disk across every priced date - the fair store, the ledger the last
`run_all.py` / `daily.py` wrote, the collector's books, bars and resolutions -
and asserts an invariant that must hold whatever the markets do. None looks
at PnL. Each docstring says what a failure means; the thresholds are at the
top of the file and are loose on purpose, so a failure is a defect and not
noise. Checks skip rather than fail when their data does not exist yet.

The four groups:

1. **The collector.** Every in-session cycle with books is priced; completed
   dates have a full day of cycles; the chain is neither stale nor newer than
   the book; the spot comes from a bar and agrees with Cboe's delayed price.
2. **The engine.** Fair values are probabilities and the continuous bound
   holds; deadlines never move and horizons only shrink; touch-pending markets
   carry 1 and are never traded; **what our bars call a touch the venue
   resolves YES, and what the venue resolves YES our bars also saw** (the two
   checks that will say whether the path check and Pyth agree, once there are
   resolutions); fair values do not jump between cycles without a reason; a
   fresh reprice of the newest cycle matches the store (catches "the engine
   changed and the store was not repriced"); earnings gaps are repaired or
   excluded.
3. **The ledger.** Every fill took a level that was in the book, on the
   right token, at most half its size; fees on the price paid; no fill after
   the barrier printed; one position per market per direction; confirmed
   fills are exactly the ones whose level survived; every cap at every
   cycle; settlements agree with the venue and with arithmetic.
4. **The models.** A/B/B-simple only took edges that cleared the bar on the
   side-correct fair, never deep-OTM or chainless; C only its two shapes; B is
   inside its band whenever it trades; B-simple never grows a quadrant past
   the smallest plus slack; the control carries no forecast; the daily report
   exists and warns.

### What the first run found, and fixed

Three real defects on one day of data - which is the argument for these
checks.

1. **`empirical_longshot` bought YES at 51c and 52c** behind a 50c top of
   book. The shape says 15-50c and the model checked only the top level; the
   harness then walked the ladder to the model's limit (`emp - 4c`). Fixed:
   the order's limit now also stops at the shape's ceiling.
   `test_c_s_ladder_walk_stops_at_the_shape_s_ceiling`.
2. **`balanced_book` breached its slack by $8.** Its within-cycle tally let a
   *projected* YES raise the floor for a NO in another quadrant; the harness
   then refused the YES (market already held, MODELS 4.2) and the NO stood.
   Two fixes: `sim.project` now refuses what `execute` refuses (either side
   already held), and the model's floor for *other* quadrants is what the
   portfolio holds, not what this cycle's projections promise - a quadrant's
   own projections still raise its own floor, because a failed projection
   only makes it smaller. `test_a_projection_refuses_what_the_fill_engine_would_refuse`,
   `test_balanced_book_does_not_let_a_projected_fill_raise_the_floor`.
   `factor_neutral` used the same `project` and inherits the first fix.
3. **The 13:40Z cycle's option chain was newer than its books.** The chain is
   fetched two to three minutes after the books and is 15 minutes delayed, so
   its prints normally precede the book by ~13 minutes; the first tick of
   2026-09-16 took 26 minutes and the chain's prints were 10 minutes *newer*
   than the book. A fair value from that chain against that book is
   information the book did not have. `sim.candidate_edges` now drops any
   market whose `quote_age_s` is negative; C is unaffected (its signal is the
   bars, the chain is only a veto). This removed most of `edge_taker`'s
   13:40Z fills, which were the largest cycle of the day.

| model | before the checks | after |
|---|---|---|
| edge_taker | 49 pos, $4,480 filled, PnL opt −602.56 | **32 pos, $2,294, −216.76** |
| factor_neutral | 21 pos, $1,026, −9.10 | 17 pos, $795, −6.54 |
| balanced_book | 18 pos, $1,567, −414.25 | 25 pos, $1,378, +33.70 |
| empirical_longshot | 53 pos, $6,000, +182.03 | 55 pos, $6,000, +182.03 |

Still not a result; still the 30 September rule.

**464 unit tests pass** (435 before), plus the 26 behaviour checks, all
green on today's store. Files: `model/tests/test_behaviour.py` (new),
`model/models/empirical_longshot.py`, `model/models/balanced_book.py`,
`model/sim.py` (`project`, `candidate_edges`), `model/tests/test_models.py`.
Ledgers and reports regenerated. Nothing committed.

### Reading a failure a few days from now

- A **collector** failure is a data-quality problem on the machine, not a
  model problem: fix the collection and the date is still usable as far as
  it goes.
- An **engine** failure on `test_the_store_matches_what_the_engine_computes_today`
  means code changed after pricing: reprice every date with
  `run_fair.py --date` (or delete the partition and let `daily.py` refill it).
  The two venue-agreement checks are the ones to watch first once markets
  start resolving; a disagreement rate above 10% either way means our bars
  and Pyth see different prints and the touch check needs the trade tape.
- A **ledger** or **model** failure is a harness or model bug. Every one of
  those checks names the fill, cycle and number it tripped on.

---

## September review 2026-10-01 — collector repair, model scores, threshold sweep

### Collector: two bugs that lost outcomes (fixed, state repaired)

1. `poly_events.refresh_events` counted a *failed* request as "Gamma has no
   such event". The 09-29 outage (14:30Z onward, every request failed) gave 204
   live events 8 misses and retired them, every September monthly among them;
   no outcome was written after 09-29 14:15Z. Now only an answered request
   without the event counts. Test `test_a_failed_request_is_not_a_miss`.
2. On the Friday daily run discovery cached the NO results before the refresh
   saw them, so they were not "newly" resolved; the event was then retired and
   pruned and 57 NO outcomes of week 09-21 never reached `resolutions`. A
   retiring event now writes every outcome. Test
   `test_retiring_event_records_outcomes_the_cache_already_had`.

Repair: 204 + 63 events revived and refreshed once (backup of the old state in
the session scratchpad only). All 1,975 September-deadline equity hit markets
now have an outcome (558 wk 09-18, 564 wk 09-25, 853 monthly). WTI/DXY monthly
still pending UMA; not traded by any model.

### Model scores, September-deadline positions only (all resolved)

Rerun `run_all.py` 2026-10-01; positions on October-deadline markets excluded
because only their touches have settled.

| model | positions | cost | PnL opt | ROI opt | confirmed cost | PnL conf |
|---|---:|---:|---:|---:|---:|---:|
| edge_taker | 200 | $16,254 | +$3,381 | +20.8% | $3,627 | +$396 |
| factor_neutral | 55 | $2,407 | −$121 | −5.0% | $632 | −$74 |
| balanced_book | 109 | $4,935 | −$1,982 | −40.2% | $787 | −$89 |
| empirical_longshot | 129 | $9,119 | −$5,588 | −61.3% | $967 | −$412 |
| random_control | 99 | $12,042 | −$163 | −1.4% | $3,939 | −$116 |

edge_taker: 90% interval on ROI (ticker×deadline cluster bootstrap, 49
clusters) −2% to +45%. All of it is NO: NO +$5,062 on 134, YES −$1,681 on 66.
Top 5 positions are +$2,752 of the +$3,381.

### Threshold sweep (`model/opportunities.py` → `store/opportunities.parquet`)

First cycle each market side clears the bar, top level, 50% of its size,
caps $150/$500, no cash limit, edge_taker's filters. September deadlines.

| EV bar | n | $ | ROI | YES ROI | NO ROI | NO 90% CI |
|---:|---:|---:|---:|---:|---:|---|
| 0 | 609 | $36.5k | +0% | −33% | +7% | −5…+18% |
| 2c | 332 | $20.2k | +9% | −43% | +24% | +2…+46% |
| 3c | 234 | $15.4k | +10% | −51% | +31% | +7…+53% |
| 5c | 135 | $7.6k | +35% | −50% | +78% | +42…+159% |
| 10c | 53 | $1.9k | +38% | −26% | +78% | +16…+152% |

Confirmed fills are 15–25% of these dollars throughout.

### Calibration (YES fair at each market's first priced cycle)

Brier: fair_atm 0.116, fair_K 0.117, fair_cont 0.119, empirical 0.128, mid
0.150. Every estimate overshot the realised touch rate (0.26 vs fair_atm 0.28,
mid 0.33): a quiet month. By engine − mid:

| engine − mid | n | mid | fair_atm | realised |
|---|---:|---:|---:|---:|
| < −10c | 122 | 0.475 | 0.094 | 0.123 |
| −10…−3c | 273 | 0.188 | 0.137 | 0.147 |
| ±3c | 359 | 0.283 | 0.273 | 0.262 |
| +3…+10c | 56 | 0.448 | 0.501 | 0.268 |
| > +10c | 97 | 0.499 | 0.811 | 0.753 |

The engine is right when it is below the market (NO buys) and wrong in the
+3–10c band, which is where YES buys come from.
