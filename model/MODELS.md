# Simulation design: three models (and one deferred) against Polymarket hit markets

Written 2026-09-18 from the 09-16 session (21 clean cycles), the collector
code, and live probes of the venue APIs. `FINDINGS.md` holds the earlier
measurements this builds on; this file is the spec the model code is built
against. Sections are numbered so the Opus prompts in `OPUS_PROMPTS.md` can
point at them.

---

## 1. What the data says, in ten lines

1. **Hit markets are the live product.** ~600 open equity one-touch markets
   across 24 tickers, weekly (Mon–Fri) and monthly, plus SPX year-end and four
   commodity/FX ladders. Median top-of-book spread 7¢. `close_above` ladders
   are ~19¢ wide and near-dead; ignore them for trading, keep collecting them.
2. **A hit market resolves the moment its barrier prints** during regular
   hours on Pyth 1-minute candles. YES holders are paid early; NO holders wait
   to period end. Gamma's `closedTime` is therefore the touch time (now stored
   as `poly_markets.closed_time` / `resolutions.settled_at_utc`).
3. **Polymarket lists new barriers around 12:40 ET every session** (164 new
   hit markets on 16–17 Sep, all in that hour), nearest to spot. A quarter of
   touched markets resolve within four hours of listing; median life 45 h.
   Until today those books were never collected on listing day. Fixed
   (`poly_events.py`).
4. **Capacity is the binding constraint, not edge.** Dollars available on the
   YES token within 5¢ of the top ask, per market, median: $52 (ask <10¢),
   $126 (10–30¢), $223 (30–70¢), $399 (70–90¢). On the NO token (mostly >90¢):
   $719. Across all equity hit markets at one snapshot: ~$89k on YES, ~$616k on
   NO. Fill price degrades fast: FINDINGS measured 0.114 → 0.145 → 0.591 for
   $0 / $100 / $500 on one ladder.
5. **Costs.** Taker fee `0.04 × min(p, 1−p)` per share; makers are rebated 25%
   of it. YES ask + NO ask sums to ~1.07 at the top (overround is the spread,
   there is no separate vig).
6. **Displacement has a shape.** Mid vs option-implied fair, 9,841 obs: under
   5¢ the market is ~2× fair (68% of markets above fair); 15–65¢ the market is
   2–3¢ *below* fair. Favourite-longshot bias, gambler signature.
7. **Cboe is 15 minutes delayed.** Latest option print 15:38Z at a 15:53Z
   fetch; the Cboe spot matches the 15:35Z bar. The stored
   `source_quote_time_utc` is the file's generation time, not the quote time.
   Use `max(last_trade_time_utc)` per symbol per cycle as the quote time, and
   the 5-minute bar containing `book_timestamp_utc` as the spot at the book.
8. **Option chains cover the barriers.** 262/271 weekly and 278/309 monthly
   open equity hit markets have a strike with OI ≥ 100 within 2% of the
   barrier at the first expiry on or after the market end. The rest are deep
   OTM barriers where the model is unreliable anyway (§3.4).
9. **Nothing is calibrated against outcomes yet.** Every closed hit market so
   far resolved YES because only touches close early; the first NO results
   arrive at period ends and are now captured. History for Feb–Aug 2026 is
   reachable by slug (`what-price-will-{ticker}-hit-in-{month}-2026`) with
   outcomes and hourly prices, which is the fastest route to a calibration
   test (prompt P1).
10. **The book is internally coherent.** 7 arbitrage violations in 39,563
    comparisons, ~$1 total. All value is in the comparison with options.

## 2. The bet, precisely

Buy at the ask (YES token or NO token), pay the fee once, hold to resolution.
Exiting costs 18–48% round trip so no model here sells before resolution; a
model that wants to is welcome to argue with the numbers in FINDINGS §3.3.

Per-fill economics for a YES purchase at price `p` on a market with fair
probability `q`:

```
cost      = p + 0.04 * min(p, 1-p)            per share
EV        = q - cost
ROI       = EV / cost
payoff    = 1 at touch time (YES) or 0 at period end
```

For a NO purchase at price `p_no` (the NO token's ask, equal to 1 − YES bid):
`EV = (1 − q) − p_no − 0.04·min(p_no, 1−p_no)`, paid at period end.

**Minimum edge** is a model parameter, not a constant. Start with EV ≥ 3¢
*and* ROI ≥ 8% for anything priced under 50¢; for NO purchases above 90¢ use
ROI ≥ 2% and a hard per-market cap, because the fee is small but the spread
is the whole edge.

## 3. Shared fair-value engine (`model/fair.py`)

Every model calls the same engine; models differ in *which* fair they trust
and *how* they size.

### 3.1 Inputs at cycle `t` for market `m`

- `S_book`: the last print known when the book was fetched — the **open** of
  the 5-minute bar containing `fetched_at_utc` (`underlying_bars`, interval
  5m). Fallback: Cboe `last_price` from the same cycle, flagged
  `spot_source='cboe_delayed'`. *(Revised 2026-09-18 review. This line first
  said "close of the bar containing `book_timestamp_utc`": the close is a
  print from up to five minutes after the book, i.e. lookahead, and
  `book_timestamp_utc` is the venue's last change to the book, up to days
  old, while a resting order is executable at the moment it was seen.)*
- `t_quote`: `max(last_trade_time_utc)` over the symbol's Cboe rows in the
  cycle. `S_quote`: Cboe `last_price`. The IV surface is taken at `t_quote`
  and reused at `t_book` (sticky-strike over 15 minutes).
- `T`: trading time from `t_book` to the market's resolution deadline, in
  NYSE session units (use `pandas_market_calendars`). Weekly: Friday close.
  Monthly: last session close. Overnight periods carry variance but no
  monitoring (§3.3).
- `r`: 1-month T-bill from `rates` (matters little; keep it).
- `B`: barrier; `direction`: up/down.
- Skip the market if `S_book` already beyond `B`, **or if any finished
  regular-hours 5-minute bar since the period began has printed `B`**
  (touch pending), or the market is closed. *(Revised 2026-09-18 review: a
  touch is a path, not a level. The rules text counts every regular-hours
  candle from Monday of the week or the 1st of the month; a barrier that
  printed this morning is a pending YES whatever the spot does after.)*

### 3.2 Implied volatility

- Cboe rows only (`source='cboe'`); yfinance IV is biased ~10 vol points low.
- Keep contracts with `open_interest >= 100` or `volume > 0`, `bid > 0`.
- Recompute IV from the bid/ask mid with Black-Scholes (do **not** trust
  `source_iv` blindly; verify it agrees within 0.01 on liquid strikes and
  log disagreements). Use OTM options only (puts below spot, calls above).
- Two expiries: the nearest on-or-after the deadline (`e1`) and the nearest
  before it (`e0`). Interpolate total variance linearly in time.
- Two vols per market:
  - `sigma_atm`: interpolated at `K = S_quote`.
  - `sigma_K`: interpolated in log-moneyness at `K = B` (clamp to the liquid
    strike range; flag when clamped).

### 3.3 Touch probability with regular-hours monitoring

The market only sees Pyth candles between 09:30 and 16:00 ET. A barrier
breached in extended hours and recovered before the open does **not** count;
an overnight gap that opens beyond the barrier **does** (the first candle's
high/low is beyond it). The continuous-monitoring formula in FINDINGS §2
overstates touch probability, most for `hit_down` YES near the money.

Model the path per session `i = 1..n`:

```
overnight:  ln(O_i / C_{i-1}) ~ N(mu_o, (1-f) * sigma^2 * dt_i)     (gap, unobserved)
intraday:   ln(C_i / O_i)     ~ N(mu_d,  f    * sigma^2 * dt_i)     (Brownian bridge, observed)
touch in session i  <=>  O_i beyond B, or C_i beyond B, or the bridge crosses B:
    P_cross | O_i, C_i  = exp( -2 * ln(B/O_i) * ln(B/C_i) / (f * sigma^2 * dt_i) )   (both on the same side of B)
```

`f` = share of daily variance realised inside the session, estimated per
ticker from `underlying_bars` (5y daily O/C and 60d 5m). Expect 0.5–0.7 for
single names, higher for SPY. `dt_i` = 1/252 (half days by calendar).
`sigma` from §3.2; drift `mu = r − sigma²/2` split by variance share.

Compute by Monte Carlo (20k paths, antithetic) — a closed form exists but
the MC is easier to get right and to test. Two outputs per market:
`fair_K` (with `sigma_K`) and `fair_atm` (with `sigma_atm`), plus
`fair_cont` (continuous-monitoring formula, `f = 1`) as the upper bound.

Tests that must exist: (a) `f = 1`, `n → ∞` reproduces the closed-form
one-touch to within MC error; (b) touch probability is monotone in `sigma`,
`T`, and distance; (c) a barrier already inside the overnight gap gives ~1;
(d) `f = 0` reduces to the probability that some close/open is beyond `B`.

### 3.4 Reliability flags carried on every fair value

- `deep_otm`: `|ln(B/S)| / (sigma_atm * sqrt(T)) > 2.5`. FINDINGS §5.2: fair
  ranged 0.002–0.147 on one SPY barrier depending on the vol input. Models
  must not trade these on the option signal.
- `clamped_strike`, `spot_source`, `quote_age_s = t_book − t_quote`,
  `earnings_inside` (an `earnings` row with `kind='upcoming'` in
  `[t_book, deadline)`).
- `earnings_gap` *(added 2026-09-18 review)*: an earnings date in
  `[deadline, expiry_after)`. The after-expiry's variance then contains a jump
  the barrier never sees, and interpolating toward it overstates the fair.
  Where a pre-earnings expiry exists the engine carries its vol flat to the
  deadline instead (`vol_source='pre_earnings_flat'`); where none does, the
  fair is flagged and the option signal must not trade it. Earnings stamps
  are the bell (16:00 ET) whether the report is before the open or after the
  close, hence the half-open intervals.
- `spread_iv = |sigma_K − sigma_atm|`.

### 3.5 Empirical touch frequency (`model/empirical.py`, used by model C)

No options. For each ticker, from 5 years of daily bars and 60 days of 5-min
bars: the fraction of historical windows of length `h` sessions in which the
regular-hours high (low) reached `S_0 · exp(±k · sigma_realised · sqrt(h))`,
tabulated on a grid of `k` (0.25 steps to 4) and `h` (1..23). `sigma_realised`
= EWMA of daily close-to-close, half-life 20 sessions. Interpolate for the
live `(k, h)`. Also produce the same table restricted to the last 250
sessions, to see regime drift. Regular hours only: daily high/low from
yfinance is the regular session, which is what Pyth candles see.

## 4. Shared execution and accounting (`model/sim.py`)

### 4.1 Replay

Cycles in `book_timestamp_utc` order, session days only, the 09-16 session
and everything after. At each cycle the model sees: all books of that cycle,
option quotes of that cycle (quote time 15 minutes earlier), bars up to the
book time, resolutions with `settled_at_utc <= t`, and its own positions.
Nothing from later cycles.

### 4.2 Fills

- Buying YES walks the YES token's asks (`side='ask'`, `outcome_index=0`,
  `depth_rank` ascending); buying NO walks the NO token's asks
  (`outcome_index=1`). Never derive one from the other; both are stored.
- Take at most `take_fraction` (default 0.5) of each level's size. Stop at
  the first level where `price + fee >= fair − min_edge`, at the per-market
  cap, or at the model's cash.
- **Persistence check** (report both): a fill is *confirmed* only if the same
  price level still shows at least the taken size at the next cycle. A live
  taker would have hit the first book; a phantom or flashed quote would not
  fill. The confirmed series is the one to believe.
- Fee on each fill: `0.04 × min(p, 1−p) × shares`.
- One market, one direction, per model, per lifetime (no averaging down),
  unless the model explicitly re-enters after fair moves by more than 10¢.

### 4.3 Resolution

- `resolutions.result` in `{Yes, No}` closes the position at 1 or 0 at
  `settled_at_utc`; cash returns then.
- 50-50 settlements (`result` null, `outcome_prices` 0.5/0.5) pay 0.5.
- Open positions at the end of the simulation are reported three ways:
  at fair (model mark), at the last YES/NO bid (liquidation mark), and
  excluded. Headline numbers use *resolved only*.

### 4.4 Bankroll and constraints (defaults; models override)

- Bankroll $10,000, no leverage, cash only. Cash committed to NO at 95¢ is
  real money locked for up to a month: track *capital-days* per position and
  report return on capital-days, not just return on notional.
- Per-market cap $150 (YES) / $500 (NO). Per-ticker cap 10% of bankroll.
  Per-direction cap (all `hit_up` YES + `hit_down` NO = "long the stock")
  40% of bankroll.

### 4.5 Metrics per model, per day and cumulative

- Realised PnL (resolved), confirmed-fill and optimistic-fill.
- Predicted vs realised: mean fair of positions vs hit rate; reliability
  table by fair bucket; Brier score of `fair_K`, `fair_atm`, `fair_cont`,
  empirical, and the market mid — this is the scientific result, independent
  of PnL and of sizing.
- Exposure: net beta-dollar delta at each cycle (§5.2); PnL regressed on
  SPY's daily return (a beta near zero is the point of model B).
- Concentration: largest ticker share, largest single position, share of
  positions in the same direction.
- Capacity: dollars the model *wanted* vs dollars it *could* fill.
- Control: a random-selection model with the same sizing rules. If it makes
  money too, the market drifted, not the edge.

## 5. The models

They share §3–§4 and differ in signal, sizing and constraint. Each is a
class with `propose(cycle_state) -> list[Order]`; the harness fills, holds,
resolves and reports.

### 5.1 A — `edge_taker` (baseline)

Trade every market where `min(fair_K, fair_atm) − cost ≥ min_edge` and ROI
≥ 8%, excluding `deep_otm`. Size to the per-market cap, first come first
served by ROI. No portfolio constraint at all. This is the "does the
mispricing exist and survive costs" measurement; expect it to be long
volatility and long/short the whole market on some days.

### 5.2 B — `factor_neutral`

Same signal as A. Sizing is a greedy allocation by ROI subject to:

- Each position's **delta** `dP/dS` (numerical, from the engine) times
  `S × shares` gives dollar-delta; times the ticker's beta to SPY (250-day
  regression on daily bars) gives beta-dollar-delta. Keep the portfolio's net
  beta-dollar-delta within ±5% of bankroll at every cycle; a candidate that
  would push it outside is skipped or trimmed. Note YES on `hit_down` has
  negative delta, NO on `hit_down` positive.
- Per-direction cap 25% of bankroll; per-ticker 8%.
- Prefer pairs: when a `hit_up` YES and a `hit_down` YES on the same ticker
  both clear the bar, take both (a synthetic long straddle, delta near zero,
  pays on any large move). When only one side clears, the beta constraint
  decides.
- Correlation of the *residual* risk is handled by a crude but honest rule:
  positions on the same ticker count as one for the concentration cap;
  positions in the same direction across tickers count at 0.5 correlation for
  a variance budget of `(2% bankroll)²` per day, using each position's fair
  as its variance `q(1−q)·notional²`.

This is the model that should survive a +4% or −4% market day. The report
must show its PnL on the worst SPY day of the window next to A's.

#### 5.2b — `balanced_book` (B without the Greeks)

The user's proposal (2026-09-18): hold the same amount of YES and NO so the
correlated risk cancels and profit converges to the summed edge. The idea is
right, the axis is wrong. YES/NO is not the risk axis of a hit market:

| position | stock exposure | vol exposure |
|---|---|---|
| YES on `hit_up` | long | long |
| YES on `hit_down` | short | long |
| NO on `hit_down` | long | short |
| NO on `hit_up` | short | long→short |

"Equal YES and NO" can be entirely long the market (YES `hit_up` + NO
`hit_down`), and equal dollars are not equal sensitivity: a 30¢ YES moves
several cents on a 1% stock move, a 95¢ NO barely moves. So the simple
version balances the **four quadrants** instead: for each new order, the
quadrant it lands in must not exceed the smallest quadrant by more than
`balance_slack` (default $500 of max loss, where max loss = dollars paid).
Per-ticker cap as in B. No deltas, no betas; the rigorous hedge is B and the
two are reported side by side so the value of the Greeks is measured, not
assumed.

On convergence: the law of large numbers needs *independent* bets. ~600
markets on 24 tickers over a few weeks are closer to 50–100 independent
bets a month, so two months of simulation will not make PnL converge to the
edge; what converges first is the calibration table (§4.5), and that is the
result to read.

### 5.3 C — `empirical_longshot` (no options)

Signal: the empirical touch frequency (§3.5) instead of implied vol. Trades
only two shapes, chosen for the displacement in §1.6:

- **Sell longshots:** buy NO when the YES ask ≤ 12¢, empirical touch
  frequency ≤ half the YES price, and the option engine (as a veto only, not a
  signal) does not put `fair_K` above the YES ask. ROI floor 2%, per-market
  cap $500, per-direction cap 30% — a crash touches every `hit_down`
  longshot at once, and that is the risk this model carries.
- **Buy the middle:** buy YES when the ask is 15–50¢ and the empirical
  frequency exceeds cost by ≥ 4¢.

Its diagnostic is the reliability table: does the historical frequency
predict touches better than implied vol? It also carries a hard rule to stop
adding `hit_down` NO for the rest of the day once SPY is down more than 1.5%
intraday (the one situation where longshots stop being longshots).

### 5.4 D — `maker` (deferred, not in the first pass)

Resting bids at `fair − margin` would earn the spread and the 25% rebate, but
in a book this thin a resting bid is mostly filled by someone who has just
watched the stock move against it: the fill *is* the adverse selection.
Simulating it honestly needs the trade tape, a cancel rule keyed to the
underlying's move since the order was placed, and a queue model. The user
judged that complexity not worth carrying now (2026-09-18). Revisit only if
models A–C show the edge is real but capacity-bound, which is the one case
where being the maker changes the answer.

### 5.5 Not built now

- **Kalshi year-end index ladders** vs SPX options: 1–4¢ spreads, a cleaner
  digital, but capacity unknown. Worth a look after the first month.
- **Early exit / convergence** trading: ruled out by round-trip cost.
- **Cross-venue arbitrage**: no overlapping contracts found.

## 6. Data contract (what the model code reads, and the traps)

| table | use | trap |
|---|---|---|
| `poly_books` | books; key on `market_id` + `outcome_index`; `depth_rank=0` is top | `(ticker, level, type)` is not unique across expiries; `book_timestamp_utc` is the venue time, `fetched_at_utc` ours |
| `poly_markets` | metadata; latest row per `market_id`; `created_at`, `closed_time`, `result` | rows before 2026-09-18 were backfilled from the raw archive; a market appears here the cycle it was first seen |
| `resolutions` | `result`, `settled_at_utc`; latest row per `market_id` | `result` null with `closed=true` = pending UMA or 50-50; check `outcome_prices` |
| `option_quotes` | Cboe rows; `open_interest`, `bid/ask`, `last_trade_time_utc` | 15 min delayed; `source_quote_time_utc` is file time. 09-16 rows were repaired (−4h) on 2026-09-18 |
| `underlying_bars` | 5m bars = spot at book time; daily bars = history, betas, `f` | yfinance regular session only, which matches Pyth's window |
| `underlying_quotes` | Cboe spot at quote time, `iv30` | delayed like the chain |
| `rates`, `earnings`, `dividends` | r, earnings flag | — |
| `poly_price_history` | hourly mid since listing (full history from 2026-09-18) | only a proxy for the mid, no depth |

Session filter: `cycle_id` between 13:30Z and 20:00Z (DST until 1 Nov). The
09-17 session is unusable (laptop asleep: 4 truncated cycles); drop it.

## 7. Work split

Done here (needed judgement, or lost data every day it waited):

- this design; the data audit in §1; capacity and delay measurements;
- collector: outcomes and touch times now stored; every known event
  refreshed each tick (2 requests) and one extra discovery walk at session
  open; full price history on first sight; old partitions migrated; 09-16
  timestamps repaired.

Delegated with prompts (`OPUS_PROMPTS.md`), in order: P1 historical
calibration, P2 fair-value engine, P3 harness, P4 models, P5 daily report,
P6 review. P1 can run before the collector has a month of data and is the
single most informative thing to do next. Every pass appends to
`model/HANDOFF.md`, which is what the verification pass reads first.
