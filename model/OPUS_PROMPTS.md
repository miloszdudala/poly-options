# Prompts for the implementation passes

Paste one prompt at a time into a fresh Claude Code session in this
repository, in order. P1 is independent and needs no option data; P2 → P3 →
P4 → P5 are sequential; P6 is a review pass after P4. Each prompt tells the
model to read `model/MODELS.md` first; section numbers refer to it.

Every prompt ends with the same **Finish** block. It is there because the
last implementation pass left work that could not be verified. Do not trim
it.

---

## P1 — Historical calibration, Feb–Aug 2026 (no options needed)

```
Read model/MODELS.md sections 1, 3.5, 4.5 and 6, then FINDINGS.md sections
2, 3.2 and 5.3, then data/src/poly_events.py, data/src/poly_discover.py
(market_row, resolution_result) and data/src/poly_books.py
(collect_price_history). Follow the conventions you see there: explicit
Arrow schemas in schemas.py, write_table, the Http class with retries,
docstrings that record measured facts.

Goal: the fastest possible test of whether Polymarket hit markets are
mispriced, using markets that already resolved. No option data exists for
those months; the test is price-versus-outcome calibration plus an
empirical-volatility fair value.

Verified 2026-09-18: closed events answer by slug:
  GET https://gamma-api.polymarket.com/events?slug=what-price-will-{ticker}-hit-in-{month}-2026
  ({ticker} lower case, {month} february..august; pltr-hit-in-august-2026
  returned 1 closed event with 34 markets). Close-above ladders use
  {ticker}-above-in-{month}-2026. Weekly slugs look like
  will-{ticker}-hit-week-of-{month}-{day}-2026; probe a few.
  Each market carries outcomePrices ('["1","0"]' on resolution), closedTime,
  createdAt, umaResolutionStatus. Hourly price history per token:
  GET https://clob.polymarket.com/prices-history?market=<tokenId>&interval=max&fidelity=60
  Gamma also accepts up to 100 `id` params per request.

Build:
1. data/src/poly_backfill.py, run as `python data/src/poly_backfill.py`.
   For tickers in data/config/underlyings.yaml tiers tier1+tier2 and months
   February..August 2026: fetch each event by slug (both patterns), write
   poly_markets rows via market_row, resolutions rows via
   poly_books.resolution_row, and hourly price history via
   collect_price_history (it fetches interval=max the first time it sees a
   token). Log slugs that return nothing. Idempotent: a second run writes
   nothing new. Pace requests: at most 8 concurrent, and stop on the first
   HTTP 429 rather than retrying through it.
2. model/calibration.py and model/run_calibration.py, writing
   model/reports/calibration_<date>.md with:
   a. Reliability table: for every hit market and every hourly price point
      while it was open, bucket the YES price (<3c, 3-5, 5-10, 10-20, 20-35,
      35-50, 50-65, >65) and report count, mean price, realised YES rate,
      and the gap with a binomial 95% interval. Split by weekly/monthly, by
      hit_up/hit_down, and by time-to-deadline bucket. State that a price
      point is a mid proxy, not a tradeable quote.
   b. The same table for the NO side (1 - price).
   c. Empirical fair value (MODELS 3.5) from underlying_bars at each price
      point, with its Brier score against the outcome next to the market
      price's Brier score, and the continuous one-touch formula with
      realised vol (EWMA 20 sessions) as a third column.
   d. Paper PnL of "buy NO whenever YES price <= 10c" and "buy YES whenever
      empirical fair - price >= 5c", one unit per market, paying price +
      3.5c (half a 7c spread) plus the 4% fee, held to resolution. By month,
      so a single crash month is visible.
   e. Exclude September: touched markets resolve early and untouched ones
      have not resolved yet, so the NO side would be wrong.

Rules: never average (bid - fair) or (fair - ask); compare mid vs fair for
displacement and fair vs paid price for a bet. Key everything on market_id.
Every rate in a report has its count next to it.

Finish - all of these, none optional:
- `.venv/bin/python -m pytest data/tests model/tests -q` passes; add tests
  for every new pure function (slug builder, bucketing, Brier, PnL rule).
- Append a section to model/HANDOFF.md titled "P1 <date>" with: files
  created or changed (path + one line each); exact commands to reproduce;
  every measured number you relied on (event counts by month, slugs that
  returned nothing, request count, runtime, rows written); every place you
  deviated from MODELS.md and why; open questions. Nothing else goes in
  that file.
- New dependencies go in requirements.txt; none without a reason in
  HANDOFF.md.
- Do not touch data/src/ except the new module; do not change any existing
  schema; do not commit; leave no scratch files in the repository.
- The final message is the summary table from the report, verbatim.
```

## P2 — Fair-value engine
 
```
Read model/MODELS.md section 3 in full, then FINDINGS.md sections 2, 4 and
5, then data/src/schemas.py and README.md "Query" and "Option quotes are 15
minutes stale". The engine is the one piece every model shares; correctness
matters more than speed.

Build model/data.py, model/fair.py, model/empirical.py per MODELS 3:
1. model/data.py is the only place that reads Parquet (DuckDB,
   read_parquet with hive_partitioning=true). Functions returning pandas
   frames for one cycle: books, Cboe option chain per symbol, spot at book
   time from 5-minute bars, rates, earnings, resolutions up to a time.
   Session filter and the 2026-09-17 exclusion live here.
2. Black-Scholes IV inversion from the bid/ask mid on OTM contracts with
   open_interest >= 100 or volume > 0 and bid > 0. Log where own IV differs
   from source_iv by more than 0.01 on liquid strikes; Cboe should agree.
3. Variance interpolation across the two expiries around the deadline;
   log-moneyness interpolation for sigma_K; clamp to the liquid range and
   flag it.
4. Regular-hours touch probability by Monte Carlo per MODELS 3.3, with f
   per ticker from estimate_intraday_share(ticker) on bars, plus the
   continuous closed form as fair_cont. numpy, 20k antithetic paths; ~600
   markets in under a minute per cycle.
5. Numerical delta dP/dS (bump spot 0.5%) and every flag of MODELS 3.4.
6. Output one row per (cycle_id, market_id): fair_K, fair_atm, fair_cont,
   fair_empirical, delta, sigma_K, sigma_atm, f, T_sessions, quote_age_s,
   spot_source, flags. Persist to model/store/fair/date=.../*.parquet with
   an explicit Arrow schema in model/schemas.py.
7. model/run_fair.py --date YYYY-MM-DD computes every session cycle of that
   date and prints markets priced, markets flagged by flag, median
   quote_age_s, and runtime.

Tests in model/tests/: the four of MODELS 3.3 (f=1 reproduces the closed
form within MC error; monotone in sigma, T and distance; barrier inside the
overnight gap gives ~1; f=0 reduces to close/open beyond B); an IV
round-trip; an interpolation test; and one end-to-end test on cycle
20260916T155117Z-5639a2 asserting PLTR hit_down 165 gets fair_K in
[0.20, 0.45] and fair_cont > fair_K.

Rules: never average (bid - fair) or (fair - ask). Quote time is
max(last_trade_time_utc) per symbol per cycle, never source_quote_time_utc.
Key everything on market_id.

Finish - all of these, none optional:
- `.venv/bin/python -m pytest data/tests model/tests -q` passes.
- Append "P2 <date>" to model/HANDOFF.md: files created or changed; exact
  commands; measured numbers (runtime per cycle, markets priced and flagged
  on 2026-09-16, f per ticker, IV disagreement rate); deviations from
  MODELS.md and why; open questions.
- New dependencies in requirements.txt with a reason in HANDOFF.md.
- Do not touch data/; do not commit; no scratch files in the repository.
- Final message: the run_fair.py output for 2026-09-16, verbatim.
```

## P3 — Simulation harness

```
Read model/MODELS.md sections 2, 4 and 6 in full, then model/data.py,
model/fair.py and model/HANDOFF.md. Build model/sim.py, the
replay-execute-resolve-report loop every model runs inside, and
model/factors.py for betas.

1. Replay per MODELS 4.1: cycles in book_timestamp_utc order, session days
   only, 2026-09-17 excluded. Each CycleState carries the books, the fair
   values from model/store/fair, bars to that time, resolutions with
   settled_at_utc <= t, and the portfolio. A model can never see a later
   cycle; enforce it in code, not by convention.
2. Model interface: a class with propose(state) -> list[Order(market_id,
   outcome_index, max_usd, limit_price, reason)] and a name.
3. Fills per MODELS 4.2: walk the right token's asks by depth_rank,
   take_fraction per level, the stop rule, the fee on the price paid, and
   the persistence check against the next cycle's book. Keep the optimistic
   and the confirmed ledger side by side.
4. Resolution per 4.3; bankroll and caps per 4.4 as parameters with those
   defaults.
5. Metrics per 4.5, including the random-selection control and the
   beta-dollar-delta series (betas from a 250-session regression of daily
   returns on SPY, computed only from bars before the cycle).
6. Per run: model/reports/<model>_<from>_<to>.md with the metric tables and
   model/store/ledger/<model>/*.parquet with every fill, mark and
   resolution, explicit schemas in model/schemas.py.

Tests: a synthetic two-cycle fixture where a level disappears between
cycles (the confirmed ledger must exclude it); YES paid at settled_at_utc
and NO at the deadline; the fee; a cap; and a no-lookahead test where a
model that reads anything from a later cycle raises.

Rules: never average (bid - fair) or (fair - ask). Key on market_id and
outcome_index. Every rate in a report has its count and dollars next to it.

Finish - all of these, none optional:
- `.venv/bin/python -m pytest data/tests model/tests -q` passes.
- Append "P3 <date>" to model/HANDOFF.md: files created or changed; exact
  commands; measured numbers (cycles replayed, runtime, fills optimistic vs
  confirmed for the random control); deviations from MODELS.md and why;
  open questions.
- New dependencies in requirements.txt with a reason in HANDOFF.md.
- Do not touch data/; do not commit; no scratch files in the repository.
- Final message: the random control's report summary table, verbatim.
```

## P4 — The models

```
Read model/MODELS.md section 5 in full, then model/sim.py, model/fair.py,
model/empirical.py and model/HANDOFF.md. Build model/models/edge_taker.py,
factor_neutral.py, balanced_book.py, empirical_longshot.py, each a class
against the harness interface, and model/run_all.py that runs every model
plus the random control over the available window and writes the reports.
Model D (maker) is deferred; do not build it.

Requirements are in MODELS 5.1-5.3 and 5.2b; do not soften them:
- A edge_taker: no portfolio constraint at all; that is its purpose.
- B factor_neutral: the beta-dollar-delta band, direction and ticker caps,
  the pair preference, the 0.5-correlation variance budget. Its report
  shows PnL on the best and worst SPY days next to A's.
- B-simple balanced_book: the four-quadrant balance of 5.2b with the slack
  parameter, no Greeks; reported next to B.
- C empirical_longshot: the two shapes only, the option engine as a veto
  only, the SPY -1.5% intraday stop.

Tests on synthetic states: A takes everything above the bar; B refuses a
position that breaks the beta band and accepts it once an opposite one
exists; balanced_book refuses a fourth long-stock position when the
short-stock quadrant is empty and slack is used up; C never buys YES above
50c and stops adding hit_down NO once the SPY rule fires.

Rules: never average (bid - fair) or (fair - ask). Key on market_id and
outcome_index. Every number in a report has a count next to it.

Finish - all of these, none optional:
- `.venv/bin/python -m pytest data/tests model/tests -q` passes.
- Run model/run_all.py on the data present.
- Append "P4 <date>" to model/HANDOFF.md: files created or changed; exact
  commands; per model the wanted vs filled dollars, positions, resolved
  PnL confirmed and optimistic, worst day; deviations from MODELS.md and
  why; open questions.
- New dependencies in requirements.txt with a reason in HANDOFF.md.
- Do not touch data/; do not commit; no scratch files in the repository.
- Final message: the run_all.py summary table, verbatim.
```

## P5 — Daily report

```
Read model/MODELS.md section 4.5, model/run_all.py and model/HANDOFF.md.
Build model/daily.py, one command that: (1) computes fair values for any
session cycles not yet in model/store/fair; (2) re-runs every model over
the whole window; (3) writes model/reports/daily_<date>.md with, per model,
cumulative resolved PnL (confirmed fills), open positions at fair and at
bid, the reliability table so far, worst day, net beta-dollar-delta today,
wanted vs filled dollars today; and collector gaps from the `runs` table
(missing cycles, truncated runs, failures). Under 120 lines, tables not
prose. Add scheduling/com.polyoptions.report.plist at 18:00
America/New_York using the install.sh conventions (read
scheduling/install.sh and extend its LABELS list).

Finish - all of these, none optional:
- `.venv/bin/python -m pytest data/tests model/tests -q` passes.
- Run model/daily.py once.
- Append "P5 <date>" to model/HANDOFF.md: files created or changed; exact
  commands; runtime; deviations and open questions.
- Do not touch data/src; do not commit; no scratch files in the repository.
- Final message: the daily report, verbatim.
```

## P6 — Review pass

```
Read model/MODELS.md and model/HANDOFF.md, then everything under model/.
You are reviewing, not building. Look for: lookahead (any read of data
timestamped after the cycle being replayed, including bars, resolutions,
betas, f, and the persistence check used for anything but confirming a
fill); fills walking the wrong token; fees on notional instead of on the
price paid; any average of (bid - fair) or (fair - ask); trading a market
that is closed or already beyond its barrier; the regular-hours correction
applied twice or not at all; reports where a rate has no count.

For each finding: file:line, a concrete failing input, the fix. Write a
failing test first, then fix, then re-run `.venv/bin/python -m pytest
data/tests model/tests -q` and model/run_all.py.

Finish - all of these, none optional:
- Append "P6 review <date>" to model/HANDOFF.md: every finding with its
  status (fixed / not a bug / left open and why), tests added, and whether
  the run_all.py numbers changed and by how much.
- Do not commit; no scratch files in the repository.
- Final message: the findings list and the before/after summary table.
```
