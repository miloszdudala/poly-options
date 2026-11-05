# Insights: the data, the engine, the accounting, and what we have learned

Split out of `MODELS.md` on 2026-10-09. `MODELS.md` says what each model and
type does and how it has done; everything they stand on is here. Revised
2026-11-04 for the refactor: sections describing removed code were rewritten
or deleted, and every takeaway not decided in `CLAUDE.md` is labelled
*(unapproved)*.
Section numbers 1–4 and 6 are unchanged from the old `MODELS.md`, because code
comments cite them as "MODELS §3.1", "MODELS 4.2" and so on: read those as
this file. `FINDINGS.md` holds the first measurements (2026-09-16/18).

---

## 1. What the data says, in ten lines

1. **Hit markets are the live product.** ~600 open equity one-touch markets
   across 24 tickers, weekly (Mon–Fri) and monthly, plus SPX year-end and four
   commodity/FX ladders. Median top-of-book spread 7¢. `close_above` ladders
   are ~19¢ wide and near-dead (measured 2026-09; they now have types of their
   own, `MODELS.md`); "ignore them for trading" was the takeaway then
   *(unapproved)*.
2. **A hit market resolves the moment its barrier prints** during regular
   hours on Pyth 1-minute candles. YES holders are paid early; NO holders wait
   to period end. Gamma's `closedTime` is therefore the touch time (now stored
   as `poly_markets.closed_time` / `resolutions.settled_at_utc`).
3. **Polymarket lists new barriers around 12:40 ET every session** (164 new
   hit markets on 16–17 Sep, all in that hour), nearest to spot. A quarter of
   touched markets resolve within four hours of listing; median life 45 h.
   Until today those books were never collected on listing day. Fixed
   (`poly_events.py`).
4. **Capacity is the binding constraint, not edge** *(unapproved)*. Dollars available on the
   YES token within 5¢ of the top ask, per market, median: $52 (ask <10¢),
   $126 (10–30¢), $223 (30–70¢), $399 (70–90¢). On the NO token (mostly >90¢):
   $719. Across all equity hit markets at one snapshot: ~$89k on YES, ~$616k on
   NO. Fill price degrades fast: FINDINGS measured 0.114 → 0.145 → 0.591 for
   $0 / $100 / $500 on one ladder.
5. **Costs.** Taker fee `0.04 × p × (1−p)` per share (the market's own schedule at the time); makers are rebated 25%
   of it. YES ask + NO ask sums to ~1.07 at the top (overround is the spread,
   there is no separate vig).
6. **Displacement has a shape.** Mid vs option-implied fair, 9,841 obs: under
   5¢ the market is ~2× fair (68% of markets above fair); 15–65¢ the market is
   2–3¢ *below* fair. Read as favourite-longshot bias, a gambler signature
   *(unapproved)*.
7. **Cboe is 15 minutes delayed.** Latest option print 15:38Z at a 15:53Z
   fetch; the Cboe spot matches the 15:35Z bar. The stored
   `source_quote_time_utc` is the file's generation time, not the quote time.
   The engine uses `max(last_trade_time_utc)` per symbol per cycle as the quote
   time, and the open of the 5-minute bar containing `fetched_at_utc` as the
   spot at the book (§3.1).
8. **Option chains cover the barriers.** 262/271 weekly and 278/309 monthly
   open equity hit markets have a strike with OI ≥ 100 within 2% of the
   barrier at the first expiry on or after the market end. The rest are deep
   OTM barriers where the model is unreliable anyway (§3.4).
9. **Calibration.** The hit-market pricing was scored against 7,750 resolved
   observations on 2026-11-03 (`CLAUDE.md`, Decided): ATM vol Brier 0.0912,
   barrier-strike vol 0.0922. Early in the window every closed hit market
   resolved YES, because only touches close early; any score on a window
   shorter than the markets' horizons has that bias (`results.universe_scores`
   reports it).
10. **The book is internally coherent.** 7 arbitrage violations in 39,563
    comparisons, ~$1 total. All value is in the comparison with options.

## 2. The bet, precisely

Buy at the ask (YES token or NO token), pay the fee once, hold to resolution.
Exiting costs 18–48% round trip so no model here sells before resolution; a
model that wants to is welcome to argue with the numbers in FINDINGS §3.3.

Per-fill economics for a YES purchase at price `p` on a market with fair
probability `q`:

```
cost      = p + rate * p * (1-p)              per share (rate normally 0.04)
EV        = q - cost
ROI       = EV / cost
payoff    = 1 at touch time (YES) or 0 at period end
```

For a NO purchase at price `p_no` (the NO token's ask, equal to 1 − YES bid):
`EV = (1 − q) − p_no − rate·p_no·(1−p_no)`, paid at period end.

**The bar** is declared by each model (`models.TODAYS_BAR` holds today's, in
one place): EV ≥ 3¢ and ROI ≥ 8%; above a 90¢ ask, ROI ≥ 2%; every YES charged
a 2¢ risk premium first (§7.2). Open in `CLAUDE.md`: only the premium is
approved. There are no per-market caps (`CLAUDE.md`, Decided: max bets).

## 3. Fair values (`model/engine.py`, `model/fair.py`, `model/types/`)

Every model reads the same fair for a market; models differ in what they buy
and how much.

### 3.1 Inputs at cycle `t` for a hit market

- `S_book`: the last print known when the book was fetched — the **open** of
  the 5-minute bar containing `fetched_at_utc` (`underlying_bars`, interval
  5m). Fallback: Cboe `last_price` from the same cycle, flagged
  `spot_source='cboe_delayed'`. *(Revised 2026-09-18 review: the bar's close
  is a print from up to five minutes after the book, i.e. lookahead, and
  `book_timestamp_utc` is the venue's last change to the book, up to days
  old.)*
- `t_quote`: `max(last_trade_time_utc)` over the symbol's Cboe rows in the
  cycle. `S_quote`: Cboe `last_price`. The smile is taken at `t_quote` and
  reused at `t_book` (sticky-strike over 15 minutes). A chain more than 30
  minutes older than the book (`engine.STALE_CHAIN_S`) is treated as absent.
- `T`: NYSE session time from `t_book` to the deadline
  (`pandas_market_calendars`), the deadline being the last session close at
  or before `end_date`.
- `r`: the 1-month T-bill from `rates`.
- A market whose spot is already beyond `B`, **or whose barrier a finished
  regular-hours bar has printed since the market began being watched**, is
  `touch_pending`: fair 1, not traded. *(Revised 2026-09-18 review: a touch
  is a path, not a level.)*

### 3.2 Implied volatility

- Cboe rows only (`source='cboe'`); yfinance IV is biased ~10 vol points low.
- Contracts with `open_interest >= 100` or `volume > 0`, and `bid > 0`.
- IV recomputed from the bid/ask mid with Black-Scholes, OTM options only
  (puts below spot, calls above); disagreements with `source_iv` past 0.01 on
  liquid strikes are logged.
- One expiry: the first whose 16:00 ET close is at or after the deadline.
  `sigma_atm` is its vol at `K = S_quote`, moved from the calendar clock to
  the session clock by total variance (`fair.expiry_vol`).

### 3.3 Touch probability (Decided)

`fair = touch_probability(S_book, B, T_sessions, sigma_atm, r, f)`. The venue
watches regular-hours candles only: a move beyond the barrier that reverses
overnight does not count, an open beyond it does. So each session is an
unwatched overnight gap carrying `1-f` of the day's variance, then a watched
session carrying `f`, simulated with a Brownian bridge inside the session.
`f = var(session) / (var(gap) + var(session))` from the stock's past daily bars,
as known at the cycle (0.52-0.75 for most stocks, 0.6 if under 60 sessions).

Scored on 9,272 observations of markets past their deadline, one look a day:
regular hours + ATM Brier 0.0788, regular hours + barrier-strike vol 0.0792,
continuous + ATM 0.0799, continuous + barrier-strike vol 0.0811. Against the
continuous formula it lowers fair by a median 0.5¢ (p90 3.9¢). Moving `f` from
0.5 to 0.75 moves fair by a median 0.4¢ (p90 3.3¢).

### 3.4 Reliability flags carried on every hit fair

- `deep_otm`: `|ln(B/S)| / (sigma_atm * sqrt(T)) > 2.5`. FINDINGS §5.2: fair
  ranged 0.002–0.147 on one SPY barrier depending on the vol input. Not
  traded.
- `no_chain`, `stale_book`, `clamped_atm`, `exact_expiry` (the expiry closes
  at the deadline), `earnings_inside` (an earnings date in
  `[t_book, deadline)`).
- `earnings_gap`: an earnings date in `[deadline, expiry)`. The expiry's
  variance then contains a jump the barrier never sees and the fair is biased
  high; such markets are not traded. Earnings stamps are the bell (16:00 ET)
  whether the report is before the open or after the close, hence the
  half-open intervals.

### 3.5 Priced from the chain (close-above, up/down, range, Kalshi)

`P(S_T > K) = e^{rT} (C(K1) − C(K2)) / (K2 − K1)`, the call spread around `K`,
or from the put spread below the money, at the expiry whose close is exactly the
deadline (`model/types/_chain.py`).
No exact expiry: the market is skipped and counted. On a flat-vol chain this
is `N(d2)`; with strikes wide against the move to expiry it carries
discretisation error. WTI hit markets: `model/types/_wti.py`.

## 4. Shared execution and accounting (`model/sim.py`, `model/results.py`)

### 4.1 Replay

One model on one type under one account. The type's cycles in `cycle_id`
order, session days only; a cycle's clock is the newest `fetched_at_utc` in
its books. At each cycle the model is offered the bets built from that
cycle's books and fair values, and sees its own holdings and the cash its
account allows. Resolutions count only once `settled_at_utc <= t`. Nothing
from later cycles.

### 4.2 Fills

- Buying YES walks the YES token's asks (`outcome_index=0`, `depth_rank`
  ascending); buying NO walks the NO token's asks (`outcome_index=1`). Never
  derive one from the other on Polymarket; both are stored. (Kalshi publishes
  bids only: a YES ask is 1 − a NO bid.)
- Take at most half of each level's size (`sim.TAKE_FRACTION`). Stop at the
  first level whose all-in cost reaches the bar's limit, or at the account's
  cash. A walk under $1 is no fill.
- A fill counts if the book had the size when we looked (`CLAUDE.md`,
  Decided; §7.1).
- Fee per type: Polymarket `rate × p × (1−p)` per share, with each market's
  recorded schedule at the time (0.04, or 0 during the 2026-10-20 to 10-30
  holiday); Kalshi index markets `0.07 × p × (1−p)` (multiplier 1 in Kalshi's API).
- One market, one side, once: a market held, or held until it settled, is
  not offered again.

### 4.3 Resolution

- `result` in `{Yes, No}` closes the position at 1 or 0 at `settled_at_utc`;
  a compounding account gets the cash then.
- 50-50 settlements (`result` null, `outcome_prices` 0.5/0.5) pay 0.5; a null
  result with other prices is a pending dispute and is not booked.
- Headline numbers use settled bets only; open bets are shown by count and
  cost.

### 4.4 Accounts

`max` (routine: no ceiling, capital used = sum of bets), `compound` (starts
at `--start`, reinvests what comes back) and `budget` (`--per-period` per
day, week from Monday or month of the type, New York time; no roll-over).

### 4.5 Metrics per model × type

- EV promised at entry (the bar's value less the all-in cost, per fill)
  against realised PnL on the same settled bets, in total and by week (month
  for monthly types) of entry.
- Predicted vs realised: reliability table by entry win probability; Brier
  score of the hit `fair` and of the market mid over the whole priced
  universe (`results.universe_scores`), independent of any model.
- Control: `random_control`, no signal. If it makes money too, the market
  drifted, not the edge.

## 6. Data contract (what the model code reads, and the traps)

| table | use | trap |
|---|---|---|
| `poly_books` | books; key on `market_id` + `outcome_index`; `depth_rank=0` is top | `(ticker, level, type)` is not unique across expiries; `book_timestamp_utc` is the venue time, `fetched_at_utc` ours |
| `poly_markets` | metadata; latest row per `market_id`; `created_at`, `closed_time`, `result` | rows before 2026-09-18 were backfilled from the raw archive; a market appears here the cycle it was first seen |
| `resolutions` | `result`, `settled_at_utc`; latest row per `market_id` | `result` null with `closed=true` = pending UMA or 50-50; check `outcome_prices` |
| `option_quotes` | Cboe rows; `open_interest`, `bid/ask`, `last_trade_time_utc` | 15 min delayed; `source_quote_time_utc` is file time. 09-16 rows were repaired (−4h) on 2026-09-18 |
| `underlying_bars` | 5m bars = spot at book time and the touch check; 1h and daily bars = the touch check before the 5m history | equities: regular session only, which matches Pyth's window; the commodity/FX ladders carry extended-hours bars |
| `underlying_quotes` | Cboe spot at quote time, `iv30` | delayed like the chain |
| `rates`, `earnings`, `dividends` | r, earnings flag | — |
| `poly_price_history` | hourly mid since listing (full history from 2026-09-18) | only a proxy for the mid, no depth |
| `kalshi_markets`, `kalshi_books` | Kalshi terms, results, and both bid ladders | books are collected hourly; results are stamped when first seen |

Session filter: 09:30–16:00 New York time (`data.in_session`, tz database).
The 09-17 session is unusable (laptop asleep: 4 truncated cycles) and is
excluded (`data.EXCLUDED_DATES`), as is any cycle whose run was not ok or
took over 900 s.

## 7. What we have learned since (2026-10-09)

### 7.1 "Confirmed" fills, and what an unconfirmed one really is

*Superseded by a decision (`CLAUDE.md`): a fill counts if the book had the
size when we looked; the ~40 s recheck showed 80–88% of filled dollars still
buyable, so there is no confirmed ledger any more. The measurements below are
what that decision rests on.*

A fill was *confirmed* if the same price level still shows at least the
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

So the confirmed series was a conservative proxy for "could a live taker have
had it", not a measurement *(unapproved)*. Whether an order sent within seconds of seeing
the quote would fill can only be measured with small live orders. There is
no sign of competing takers; there is a sign of active quoting, so a live
system has to act on the book within seconds, with option data that is
itself 15 minutes old *(unapproved)*.

**Update 2026-10-17: the 40-second recheck.** The collector now reads every
book a second time ~40 s after the first (`poly_books_recheck`). On the first
afternoon of it (10-16, 7 cycles, 15 orders), most levels the next-cycle rule
called lost were the same size re-quoted a tick or two away, in both
directions: repricing bots following the stock. 11 of 15 orders were still
fully buyable under their limit at the recheck; 6 at the next snapshot; 4
passed the strict same-price rule. The four lost even at 40 s all moved
against the bet. (The recheck-based confirmation was removed with the
confirmed ledger on 2026-11-04; the recheck is still collected.)

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
  the 31-year index measurement, e.g. 2¢. **Approved 2026-10-09.** September
  is consistent with it: every model's YES lost, and the engine overshot
  touches most where it bought YES (§7.3) *(unapproved)*.
- **Buying NO.** The premium works in our favour, but it is payment for
  carrying crash risk. Counting it as extra edge would double-count; leave
  the fair alone and control the crash risk by sizing *(unapproved; the bar
  charges NO nothing, as approved)*.
- **Buying YES as a hedge.** Then we *are* the insurance buyer, and paying the
  premium is reasonable, as it is for every investor who hedges: we pay it for
  smaller losses in the scenario we are afraid of. No correction
  *(unapproved; the approved rule charges every YES, hedges included)*.

**Built 2026-10-09**, now `TODAYS_BAR.yes_premium = 0.02` in
`models/base.py`, charged to every YES under today's bar. Effect on September
under the code of the time: optimistic PnL up in every option model,
confirmed PnL down by $150–200 per model, because of a few confirmed YES
winners. Too few to read either way.

### 7.3 Why hedging lost money in September although each bet was "+EV"

- **A hedge cannot turn +EV into −EV.** The book's EV is the sum of its bets'
  EVs; a hedge only changes variance. Where a hedged model lost, it is
  because it was made to take more of the bets that were not really +EV
  *(unapproved)*.
- **Those were the YES bets.** Where the engine sat 3–10¢ above the market
  mid (where YES buys come from), it said 50% and 27% touched; where it sat
  below the mid (NO buys) it was right (review of 2026-10-01, before that day's fixes). Selection makes this
  worse: picking the bets with the largest estimated edge picks the ones where
  the estimate is most likely too high (winner's curse), and the premium
  (§7.2) leans the same way *(unapproved)*.
- **Equal dollars are not equal risk.** A removed model balanced dollars per
  quadrant. In a calm week $500 of NO at 93¢ earns $37 and $500 of YES at 30¢
  loses $500, so its "balanced" book was a large bet on movement. 09-28 shows
  it: 37 YES bets balanced up/down, a calm two days, −$2,026 *(unapproved)*.
- **YES bets are one bet when the market is calm.** YES on hit_up and YES on
  hit_down on the same stock both lose if nothing moves. 50–100 independent
  bets a month, not 600 *(unapproved)*.

### 7.4 September was calm

SPY moved more than 1% on one day (09-21, +1.55%); realised 11% against an
implied 12.3%. 26% of priced barriers touched, against 28% option-implied and
33% at the market mid. A month that undershoots implied vol pays NO and
punishes YES before any question of edge, so September alone cannot separate
"the engine finds mispricings" from "selling vol in a calm month". October is
the test *(unapproved)*.

### 7.5 Data and harness defects found 2026-10-09 (fixed)

1. 09-23, 09-29 and 10-01 were priced before their 5-minute bars existed; the
   touch check saw no evidence and 40 fills landed on barriers already
   printed. `daily.py` now defers such cycles (`daily.bars_cover`).
2. `resolutions_upto` compared timestamps as text, so same-day settlements
   were invisible and the last day of a run never settled.
3. `daily_bars` / `bars_5m` kept an arbitrary version of a bar stored twice,
   so betas changed run to run and one model traded 115–141 fills on
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
pays for itself** *(unapproved)*. (Measured under the pricing of the time:
the Monte Carlo `fair_K`.) One month, one calm regime; re-test if markets turn
volatile, when vol moves faster. The scripts (`model/experiments/realtime_*.py`)
were removed 2026-11-04; git history has them.

### 7.7 $50 books (2026-10-17) *(unapproved)*

`model/experiments/small_book.py` (removed 2026-11-04, in git history) ran $50
books that bought the best edges only, or weekly markets bought Monday to
Wednesday / Monday to Tuesday, at EV bars 3/6/10/15c. Result over 4 weeks:
noise-level differences.
