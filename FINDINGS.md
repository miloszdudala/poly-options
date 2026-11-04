# Objective and findings

Working notes, revised by hand. `README.md` covers how the collector runs;
`MARKETS.md` covers which markets exist. This file is what we are trying to do,
what we have measured, and which mistakes not to repeat.

Last updated: 2026-09-18 (section 7 added; the model design is in `model/MODELS.md`). All numbers below come from the snapshot of
2026-09-16 15:53 ET and the 21 cycles collected overnight into 2026-09-17.
Checked against the code on 2026-11-04: what has since been decided in
`CLAUDE.md` is marked so, and takeaways not decided there are labelled
*(unapproved)*.

---

## 1. Objective

**Find prediction-market contracts that are mispriced, and make money from it.**
That is the whole objective. Everything below is a working assumption about
*how*, held because of evidence, not because it is part of the goal.

### Current working assumption: buy at the offer, hold to resolution

The bet is: buy at 0.35, get paid 1.00 or 0.00. The only execution cost is the
price paid plus the fee, once.

*Since decided (`CLAUDE.md`): every bet is held to resolution; the project is
not a round-trip trader.* At the time this was a conclusion, not a rule. It
followed from one measurement: exiting is expensive. Buying $100 and selling it straight back costs ~48% on close_above and
~18-42% on hit markets (section 3.3), so a strategy that needs to sell gives back
more than the edge. Holding to resolution is simply the only approach we can
currently see that clears its own costs.



### Status of the alternatives — these differ, and the difference matters

| approach | status |
|---|---|
| **Early exit / stop-loss** | *Ruled out on current evidence.* Exit cost (18-48% round trip) dwarfs any plausible stop. Would change if spreads tighten. |
| **Arbitrage inside Polymarket** | *Tested and absent.* 7 violations in 39,563 comparisons, ~$1 total (section 3.4). Genuinely not there. |
| **Market making / resting orders** | *Untested, not refuted.* Fees are taker-only with a 25% maker rebate, so the economics point this way. We chose not to collect the trade tape, so we cannot yet tell whether a resting order would fill, or only fill when we are wrong. Reopening this means collecting CLOB trades. |
| **Something else** | Open. |

The table is as of 2026-09-18. Holding to resolution has since become a rule
(`CLAUDE.md`); the maker row is untested still (`model/MODELS.md`).

---

## 2. The method as first measured (2026-09-16)

*Since decided (`CLAUDE.md`): the continuous one-touch below with the ATM vol
of the first expiry at or after the deadline, one fair for both sides. Steps
5 (two vols, positive under both) and the "1 - bid" NO price are not what the
code does: the NO token's own ask is used.*

For each prediction market:

1. `fair` = Black-Scholes one-touch probability from the option smile.
2. The price you pay is `ask` (to buy YES) or `1 - bid` (to buy NO).
3. Fee = `0.04 x min(p, 1-p)` per share on the price paid. *(Corrected 2026-11-04: the venue charges `0.04 x p x (1-p)`, which is what the code now uses.)*
4. `edge = fair - price - fee`.
5. Compute `fair` **twice** — once with IV at the barrier strike, once with ATM
   IV. Keep only markets positive under both.
6. Only use option strikes with `open_interest >= 100`.

One-touch, barrier `B`, spot `S`, `b = ln(B/S)`, `m = r - sigma^2/2`:

```
P(touch) = N((mT - b)/(sigma*sqrt(T))) + exp(2mb/sigma^2) * N((-b - mT)/(sigma*sqrt(T)))
```

At zero drift this is ~ `2 x digital`, which gives a model-free cross-check
straight off a call spread. (The call spread itself now prices the
close-above, up/down, range and Kalshi types, `model/types/_chain.py`.)

### The method that does NOT work — do not repeat this

Measuring edge as **`bid - fair`** or **`fair - ask`** and averaging it.

If fair sits between the bid and the ask, both of those are **negative by
construction**. It measures the width of the spread, not the market. Reading
that negative number as "the market is efficient" produced four separate wrong
conclusions in one session, including "the edge flips sign every cycle, so it is
noise."

To ask whether the market is *displaced*, compare **mid vs fair**.
To ask whether a *bet* is good, compare **fair vs the price you pay**.
Never average `bid - fair` and conclude anything from its sign.

Related: the spread is a real cost but you pay **half of it, once**, under the
current hold-to-resolution assumption. Round-trip cost only becomes relevant
again if a model proposes selling before expiry — see section 1.

---

## 3. Findings

### 3.1 There are mispricings

Of 465 hit markets with a matching liquid option expiry:

| | count |
|---|---|
| positive edge, barrier-strike IV | 194 |
| positive edge, ATM IV | 276 |
| **positive under both** | **164 (35%)** |
| ...of those, on a liquid option chain | 138 |

Median edge of the 164: **0.9c**. Best on liquid chains, one snapshot:

| ticker | market | days | buy | at | fair | edge | size avail |
|---|---|---|---|---|---|---|---|
| RKLB | hit_down 60 | 2 | YES | 0.380 | 0.54 | 14.0c | $228 |
| HOOD | hit_down 100 | 2 | YES | 0.470 | 0.60 | 11.0c | $564 |
| PLTR | hit_up 177 | 2 | NO | 0.350 | — | 7.5c | $179 |
| GOOGL | hit_down 340 | 2 | YES | 0.710 | 0.79 | 6.1c | $852 |
| PLTR | hit_down 165 | 2 | YES | 0.240 | 0.33 | 5.6c | $288 |
| NFLX | hit_down 75 | 2 | YES | 0.430 | 0.49 | 4.7c | $516 |

Roughly $450 of expected value across that table, in one snapshot.

### 3.2 Favourite-longshot bias

Mid price vs option-implied fair, 9,841 observations over 21 cycles:

| price bucket | median mid | median fair | displacement | mid > fair |
|---|---|---|---|---|
| under 5c | 0.034 | **0.017** | +0.010 | **68%** |
| 5-15c | 0.080 | 0.070 | +0.010 | 59% |
| 15-35c | 0.220 | 0.248 | -0.022 | 35% |
| 35-65c | 0.490 | 0.525 | -0.031 | 23% |

Longshots priced at roughly **2x fair**; the middle underpriced. The classic
gambler-dominated signature *(unapproved)*. Robust in direction: the skew error
(5.2) inflates fair for OTM barriers, so true longshot overpricing is *larger*
than shown *(unapproved)*.

### 3.3 Costs

- **Overround IS the spread**, exactly. A NO ask at `x` is a YES bid at `1-x` —
  same order. Verified on 629 markets, max difference 0.0. There is no separate
  vig to hunt for.
- **Fee = 4% of the price paid, flat**, for anything under 50c (because
  `0.04 * p / p`). Less above 50c: ~1% at 71c. *(Corrected 2026-11-04: the
  venue's `0.04 * p * (1-p)` is 4% of `1-p`: 3.2% at 20c, 2% at 50c, 1.2% at 70c.
  The cost percentages below were measured with the old formula.)*
- Median one-way cost as % of price, by bucket: under 5c **74%**, 5-15c 49%,
  15-35c 24%, 35-65c 15%, over 65c **8.6%**. The absolute spread barely changes
  (0.04-0.10) across the range, so cheap contracts are ruinous in percentage
  terms. **Do not buy cheap contracts** *(unapproved; no rule in the code
  rests on it: the bar judges EV and return at the price paid)*.

### 3.4 No model-free arbitrage

39,563 logical-coherence comparisons across 21 cycles (barrier ladders must be
monotone; longer expiry worth more; `close above K` <= `hit K`): **7 violations**
above 0.5c, max margin 1.26c on 20 shares. About **$1 total**. The book is
internally coherent — all value comes from the options comparison, not from
inconsistencies within Polymarket.

### 3.5 Market context

Equity prediction markets on Polymarket shrank ~90% between Feb and Aug 2026
(monthly close-above -96%, weekly -94%, hit -78%). Volume did **not** rotate from
close-above into hit markets; everything shrank. Full table in `MARKETS.md`.
Median spreads now: hit ~7c, close_above ~19c. Hit markets are the live ones
*(unapproved)*.

---

## 4. Data quality rules

- **49% of Cboe contracts never trade on a given day.** Their IV is computed from
  a placeholder quote and is meaningless. Two vendors disagree by **59 vol points
  at p90** on untraded contracts, vs 2 points on liquid ones. Always filter on
  `open_interest >= 100` or `volume > 0`.
- **Chain liquidity by name matters**: PLTR 82% of strikes traded, TSLA 77%,
  EWY **27%**, ABNB 26%, NDX 19%. EWY screened top of the list and was pure
  artifact. Restrict to liquid-chain names *(unapproved; the code filters
  contracts on open interest and volume, not names)*.
- **Cboe IV is correct; yfinance IV is not.** On identical bid/ask, yfinance runs
  ~9-11 vol points low. Verified by inverting Black-Scholes by hand: own
  calculation matched Cboe to within 0.008 at every strike. Use Cboe.
- **`poly_books`: always key on `market_id`.** `(ticker, strike, contract_type)`
  is not unique — MSFT close_above 510 is open in four months at once. `end_date`
  and `period` now ride on every book row.
- Fixed 2026-09-17: the Cboe payload's top-level `timestamp` is **UTC** while
  per-contract `last_trade_time` is **ET**. Both were parsed as ET, putting
  `source_quote_time_utc` four hours in the future.

---

## 5. Open questions

1. **Regular-hours-only monitoring.** *Since measured and decided
   (`CLAUDE.md`): a regular-hours correction moved fair by a median 1.4c and
   did not improve the Brier score (0.0941 vs 0.0945), so the continuous
   formula is used.* At the time: Polymarket resolves on Pyth 1-minute candles
   during regular hours only, so the true touch probability is lower than the
   continuous formula's.
2. **Deep-OTM barriers are unreliable.** Feeding one smile point into a
   constant-vol formula double-counts skew. SPY hit_down 700, 14 days: fair
   ranges 0.0018 (ATM IV) to 0.1466 (barrier IV) — 80x. Near-the-money barriers
   are fine: median disagreement between the two IVs is **0.006**. Discount deep
   OTM specifically, not everything. (The code flags `deep_otm` past 2.5
   standard moves and does not trade it.)
3. **Verification against outcomes.** *Since done for the hit pricing: 7,750
   resolved observations scored on 2026-11-03 (`CLAUDE.md`).* The model-free
   test (do contracts priced at 3-5c resolve YES 3-5% of the time?) is the
   reliability table in every report.
4. **Barrier prices are very spot-sensitive.** PLTR fair moved 0.39 -> 0.70 as
   spot went 170 -> 174. With 15-minute-old books, timing matters.
5. **Depth caps size** at a few hundred dollars per market.

## 6. Next steps as ranked on 2026-09-18 (since resolved)

1. Empirical touch frequencies from `underlying_bars`: built, then removed on
   2026-11-04; fair values come from option prices only (`CLAUDE.md`: not a
   modelling project).
2. Quantify the monitoring correction: done; not applied (§5.1).
3. Smile-consistent barrier pricing: not built; the decided pricing uses the
   ATM vol.

---

## 7. Added 2026-09-18: lifecycle, capacity, delay

- **Every closed hit market so far resolved YES** (539 of 539) because a touch
  closes the market early and the NO outcomes only arrive at period end. The
  collector was dropping Gamma's `outcomePrices` and `closedTime`, and events
  that close leave the open-events walk, so **no NO outcome would ever have
  been recorded.** Fixed: `poly_markets.result` / `closed_time`,
  `resolutions.result` / `settled_at_utc`, and a by-slug refresh of every
  known event on every tick (`poly_events.py`). Old rows backfilled from raw.
- **New barriers are listed ~12:40 ET daily** (164 on 16-17 Sep, all in that
  hour), nearest to spot; 25% of touched markets close within 4 h of listing,
  median life 45 h. Their books were never collected on listing day. Fixed.
- **Capacity per market, YES token, within 5c of the top ask** (median): $52
  under 10c, $126 at 10-30c, $223 at 30-70c; NO token ~$719. One snapshot
  offers ~$89k on YES and ~$616k on NO across all equity hit markets.
- **Cboe is 15 minutes delayed and `source_quote_time_utc` is the file
  time**, not the quote time; the latest print in a chain was 15 minutes
  before the fetch and spot matched the bar 15-18 minutes earlier. Use
  `max(last_trade_time_utc)` per symbol per cycle. The 09-16 rows had it four
  hours late on top of that; repaired.
- ~~Fair values must model regular-hours-only monitoring before any model
  trades.~~ Built, measured, and not adopted: `model/INSIGHTS.md` §3.3.
- **Trade tape exists** (`data-api.polymarket.com/trades`, full history per
  market, paginated) which reopens the maker question in section 1.
- **History is reachable**: closed Feb-Aug events answer by slug with outcomes
  and hourly prices, so the calibration test in 5.3 does not have to wait for
  weeks of expiries.
