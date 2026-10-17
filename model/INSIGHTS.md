# Insights: the data, the engine, the accounting, and what we have learned

Split out of `MODELS.md` on 2026-10-09. `MODELS.md` now says only what each
model does and how it has done; everything the models stand on is here.
Section numbers 1–4 and 6 are unchanged from the old `MODELS.md`, because code
comments cite them as "MODELS §3.1", "MODELS 4.2" and so on: read those as
this file. `FINDINGS.md` holds the first measurements (2026-09-16/18).

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

## 7. What we have learned since (2026-10-09)

### 7.1 "Confirmed" fills, and what an unconfirmed one really is

A fill is *confirmed* (§4.2) if the same price level still shows at least the
size we took at the **next 15-minute snapshot**. It says nothing about
whether the position is closed or resolved; it applies to every fill, open or
settled, September or October.

The Polymarket trade tape (`data-api.polymarket.com/trades`) was checked
against edge_taker's cheap NO fills (NO ask < 50¢, 97 of them, 63 with tape
covering the fill):

- In **75% of unconfirmed fills, nobody traded at our price or better in the
  following 15 minutes**; the median number of trades in that window is 0.
  The quote was moved or cancelled by whoever posted it, not taken by a
  faster buyer.
- The book had been updated a median 6 seconds before our snapshot: these
  are live quotes, not stale data.
- 23 of 97 were *cheaper* at the next snapshot. The rule demands the same
  price, so it counts an improvement as a failure.

So the confirmed series is a conservative proxy for "could a live taker have
had it", not a measurement. Whether an order sent within seconds of seeing
the quote would fill can only be measured with small live orders. There is
no sign of competing takers; there is a sign of active quoting, so a live
system has to act on the book within seconds, with option data that is
itself 15 minutes old.

**Update 2026-10-17: the 40-second recheck.** The collector now reads every
book a second time ~40 s after the first (`poly_books_recheck`). On the first
afternoon of it (10-16, 7 cycles, 15 orders), most levels the next-cycle rule
called lost were the same size re-quoted a tick or two away, in both
directions: repricing bots following the stock. 11 of 15 orders were still
fully buyable under their limit at the recheck; 6 at the next snapshot; 4
passed the strict same-price rule. The four lost even at 40 s all moved
against the bet. From then on, `sim.confirm_by_recheck` decides confirmation
where a recheck exists; older cycles keep the next-cycle rule. So confirmed
PnL before and after 10-16 is not measured the same way.

### 7.2 The volatility risk premium

**What it is.** Option prices carry the price of insurance. Holders of stock
pay to be protected from a crash, sellers demand to be paid for providing it,
so implied volatility sits above the volatility that later happens. It is
not a mispricing: selling options collects it, and loses it all back in a
crash (February 2018, March 2020).

**Measured, S&P 500, 1995-01 to 2026-09** (VIX against SPY's realised vol
over the next 21 sessions, 7,974 days):

| period | VIX − realised (vol pts) | realised / implied (median) | VIX above realised |
|---|---:|---:|---:|
| 1995–2007 | 3.7 | 0.76 | 82% |
| 2008–2009 | 2.8 | 0.79 | 79% |
| 2010–2019 | 3.7 | 0.71 | 84% |
| 2020 | 2.7 | 0.73 | 75% |
| 2021–2026 | 4.1 | 0.74 | 86% |
| **all** | **3.7** | **0.75** | **83%** |

Stable for 31 years, crash years included. VIX is built from the whole smile
and sits ~1–2 points above at-the-money vol, which is what the engine uses, so
the premium in the engine's terms is nearer 2–3 points on the index, about
10–15% of the vol level.

**Single stocks.** No implied-vol history before 2026-09-16 is in the store.
Cboe `iv30` on 09-16/18 against realised vol through 10-08, 19 equities:
median realised/implied **0.83**, below 1 for 15 of 19 (PLTR 0.48, AAPL 0.74,
NVDA 0.75 … META 1.53, ABNB 1.32). One calm fortnight: it says "there is a
premium", not how big. The academic literature finds the single-stock
premium smaller and less reliable than the index's.

**What it is worth in cents.** One-touch probability, 45% vol, zero drift,
with the vol scaled down by a premium:

| barrier, horizon | at implied | −5% vol | −10% vol | −20% vol |
|---|---:|---:|---:|---:|
| 3%, 2 days | 46.1¢ | −2.3¢ | −4.8¢ | −10.4¢ |
| 5%, 5 days | 44.1¢ | −2.4¢ | −4.9¢ | −10.5¢ |
| 8%, 5 days | 22.5¢ | −2.3¢ | −4.7¢ | −9.6¢ |
| 15%, 20 days | 27.0¢ | −2.4¢ | −5.0¢ | −10.2¢ |

A premium of 5% of the vol, which is small, moves a touch probability by
~2.4¢, almost regardless of the barrier. Our EV bar is 3¢. **A small premium
is the same size as our edge**, which is why it matters here when it would
not matter to a fund.

**When to account for it, and when not:**

- **Buying YES on the option signal (speculation).** The fair includes the
  premium, so YES looks better than it is by ~2–5¢. Here we are buying
  insurance we do not need, and paying the premium is a pure cost. This is the
  case for correcting: not by re-fitting the vol (two weeks cannot fit it),
  but by asking YES to clear the bar *plus* a premium allowance taken from
  the 31-year index measurement, e.g. 2¢. September is consistent with it:
  every model's YES lost, and the engine overshot touches most where it bought
  YES (§7.3).
- **Buying NO.** The premium works in our favour, but it is payment for
  carrying crash risk. Counting it as extra edge would double-count; leave
  the fair alone and control the crash risk by sizing (scenario_hedged).
- **Buying YES as a hedge.** Then we *are* the insurance buyer, and paying the
  premium is reasonable, as it is for every investor who hedges: we pay it for
  smaller losses in the scenario we are afraid of. No correction.

**Built 2026-10-09** as `sim.YES_RISK_PREMIUM = 0.02`, charged to every
option-signal YES in every model (MODELS, the box at the top). It is not
charged to YES bought as a hedge, because no model buys YES purely as a
hedge: scenario_hedged only trims, it never adds a YES the signal didn't
want. Effect on September: optimistic PnL up in every option model, confirmed
PnL down by $150–200 per model, because of a few confirmed YES winners.
Too few to read either way.

### 7.3 Why hedging lost money in September although each bet was "+EV"

- **A hedge cannot turn +EV into −EV.** The book's EV is the sum of its bets'
  EVs; a hedge only changes variance. Where a hedged model lost, it is
  because it was made to take more of the bets that were not really +EV.
- **Those were the YES bets.** Where the engine sat 3–10¢ above the market
  mid (where YES buys come from), it said 50% and 27% touched; where it sat
  below the mid (NO buys) it was right (review of 2026-10-01, before that day's fixes). Selection makes this
  worse: picking the bets with the largest estimated edge picks the ones where
  the estimate is most likely too high (winner's curse), and the premium
  (§7.2) leans the same way.
- **Equal dollars are not equal risk.** balanced_book balanced dollars per
  quadrant. In a calm week $500 of NO at 93¢ earns $37 and $500 of YES at 30¢
  loses $500, so its "balanced" book was a large bet on movement. 09-28 shows
  it: 37 YES bets balanced up/down, a calm two days, −$2,026.
- **YES bets are one bet when the market is calm.** YES on hit_up and YES on
  hit_down on the same stock both lose if nothing moves. 50–100 independent
  bets a month, not 600.

### 7.4 September was calm

SPY moved more than 1% on one day (09-21, +1.55%); realised 11% against an
implied 12.3%. 26% of priced barriers touched, against 28% option-implied and
33% at the market mid. A month that undershoots implied vol pays NO and
punishes YES before any question of edge, so September alone cannot separate
"the engine finds mispricings" from "selling vol in a calm month". October is
the test.

### 7.5 Data and harness defects found 2026-10-09 (fixed)

1. 09-23, 09-29 and 10-01 were priced before their 5-minute bars existed; the
   touch check saw no evidence and 40 fills landed on barriers already
   printed. `daily.py` now defers such cycles.
2. `resolutions_upto` compared timestamps as text, so same-day settlements
   were invisible and the last day of a run never settled.
3. `daily_bars` / `bars_5m` kept an arbitrary version of a bar stored twice,
   so betas changed run to run and factor_neutral traded 115–141 fills on
   identical data. Now newest fetch wins.

### 7.6 Real-time option data would not have helped (measured 2026-10-17)

Cboe's free chain is ~15 minutes delayed (median quote age 912 s against the
book). The question was whether paying for real-time options would raise
profit. Stand-in for a real-time feed: price each snapshot's books with the
**next** snapshot's chain, whose prints date from about this snapshot's book
time (median quote age ~10 s; a third of market-cycles are up to ~2 minutes
*ahead* of the book, a small lookahead that can only flatter the real-time
side). Spot, bars and touch checks are identical; only the vol changes. Both
sides re-priced with today's engine on the same cycles (every session cycle
09-16 to 10-15 but each day's last), replayed with the default models, scored
on markets whose deadline passed by 10-09:

| model | ROI delayed → real-time | PnL | confirmed PnL |
|---|---|---|---|
| edge_taker | 22.0% → 18.7% | $5,474 → $5,195 | +$152 → −$226 |
| direction_balanced | 24.7% → 16.6% | $4,574 → $3,077 | +$255 → −$214 |
| scenario_hedged | 20.6% → 16.5% | $4,351 → $3,681 | −$15 → −$211 |

The fair value barely moves: median |Δ fair_K| 0.13¢, p90 0.74¢, p99 2.8¢
over 204,643 market-cycles. That is expected, because the chain only supplies
volatility; the spot already comes from 5-minute bars at book time, and vol
moves little in 15 minutes. The PnL gap is then mostly which marginal bets
cross the bar (real-time took 28 more positions in edge_taker), compounded by
path dependence, and should be read as noise around zero rather than as
delayed data being better. **Conclusion: no evidence that premium option data
pays for itself.** One month, one calm regime; re-test if markets turn
volatile, when vol moves faster. Scripts: `model/experiments/realtime_options.py` and `realtime_compare.py`.
