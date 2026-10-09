# September, model by model: what was bought and why it paid or didn't

Written 2026-10-09. Scope: every position on a **September-deadline** market
(weeklies of 09-18 and 09-25, monthlies of 09-30), all of them resolved.
Source: the ledgers from the `run_all.py` rerun of 2026-10-09, after two bug
fixes made the same day (see the end). The numbers therefore differ a little
from the 2026-10-01 September review.

"Optimistic" counts every fill the harness made. "Confirmed" counts only fills
whose price level was still in the book at the next cycle (MODELS 4.2). Confirmed
is the series to believe. Optimistic shows what the signal *chose*.

## Scoreboard

| model | positions | cost | PnL optimistic | ROI | confirmed cost | PnL confirmed |
|---|---:|---:|---:|---:|---:|---:|
| edge_taker | 199 | $16,234 | **+$3,396** | +20.9% | $3,598 | **+$411** |
| factor_neutral | 69 | $2,359 | −$13 | −0.5% | $532 | −$37 |
| balanced_book | 111 | $4,839 | −$1,647 | −34.0% | $893 | −$62 |
| empirical_longshot | 147 | $10,240 | −$5,669 | −55.4% | $1,838 | −$692 |
| random_control | 94 | $14,092 | −$2,310 | −16.4% | $5,083 | −$877 |

One sentence per model:

- **edge_taker** made money only on NO, and mostly on cheap NO quotes that
  didn't persist to the next cycle.
- **factor_neutral** was hedged into irrelevance: small, flat, and its one
  losing idea (2-day straddles) cancelled its winning one.
- **balanced_book** was forced to buy YES to keep its quadrants even, and
  YES lost.
- **empirical_longshot** almost never sold longshots. It bought mid-priced
  YES, using a touch table built from years more volatile than September.
- **random_control** shows the baseline: a random YES loses about two-thirds of
  its cost, and a random NO roughly breaks even.

## The backdrop: September had fewer touches than anyone priced

Every model is one way or another a bet on whether a barrier gets touched, so
the month's volatility decides most of the outcome.

- SPY moved more than 1% on one day only (+1.55% on 09-21). Its realised vol
  over 09-16–09-30 was 11%, against an implied 12.3%.
- On the names traded most, realised vol was often far below implied: PLTR
  25% realised vs 46% implied, TSLA 30% vs 44%. META (75% vs 42%) and HOOD
  (64% vs 61%) were the exceptions, and those were the two names where NO
  bets hurt.
- At the market level (2026-10-01 review): 26% of barriers touched, against
  an option-implied 28% and a market mid of 33%. Everyone overshot, and the
  market overshot most.

The consequence is mechanical. **A NO is short volatility and a YES is long
it.** In a month that undershot implied vol, NO wins and YES loses, before any
question of edge. The random control shows it with no signal at all: random
YES −67%, random NO −0.1%.

So the September result can't separate "the engine finds mispricings" from
"selling vol in a calm month works". October is the out-of-sample test of
that.

---

## edge_taker: the only winner, and less than it looks

**What it does.** It buys any market where the more conservative of the two
option-implied fairs (`fair_K`, `fair_atm`) beats the ask plus fee by at
least the EV bar, with ROI ≥ 8%. Deep OTM and earnings-gap markets are
excluded. Size goes to the per-market cap ($150 YES / $500 NO), with no
portfolio constraint.

**What it bought.** 134 NO ($13,043) and 65 YES ($3,191). NO dominates
because the engine usually sits *below* the market (the favourite-longshot
bias in FINDINGS §3.2), and the NO side is where that disagreement can be
bought.

| quadrant | n | cost | PnL | ROI |
|---|---:|---:|---:|---:|
| NO hit_down | 57 | $5,376 | +$3,602 | +67% |
| NO hit_up | 77 | $7,666 | +$1,460 | +19% |
| YES hit_down | 38 | $1,745 | −$795 | −46% |
| YES hit_up | 27 | $1,446 | −$871 | −60% |

**Why the NO side paid: two very different trades are hiding in it.**

| NO bought at | n | cost | PnL | confirmed cost | confirmed PnL |
|---|---:|---:|---:|---:|---:|
| under 50¢ ("cheap NO") | 53 | $3,211 | **+$3,850** | $376 | +$439 |
| 50¢ and over | 81 | $9,831 | +$1,213 | $2,781 | −$170 |

1. **Cheap NO** means the market priced a touch at 75–85% and the engine said
   about 68%. Realised was 43%. These produced the six best trades of the
   month:

   | bet | price | engine touch prob. | result | PnL |
   |---|---:|---:|---|---:|
   | GOOGL hit_down 340, wk 09-18, bought 09-16 | 0.14 | 0.20 | no touch | +$730 |
   | AMZN hit_down 244, wk 09-18, bought 09-16 | 0.15 | 0.21 | no touch | +$721 |
   | TSLA hit_down 367.5, wk 09-25 | 0.335 | 0.40 | no touch | +$449 |
   | EWY hit_up 188, monthly | 0.27 | 0.32 | no touch | +$432 |
   | META hit_up 780, wk 09-25 | 0.22 | 0.26 | no touch | +$420 |
   | META hit_up 690, wk 09-18 | 0.36 | 0.41 | no touch | +$375 |

   But **only $376 of the $3,211 was confirmed.** For most of these quotes,
   the level the model hit was gone at the next cycle. That is what a
   flashed or stale quote looks like, and a live taker usually wouldn't have
   got it. The +$3,850 is the signal being right about *which* quotes were
   wrong. It isn't money you could have taken. The confirmed version, +$439
   on $376, is real but tiny.

2. **Expensive NO** (90–95¢, "sell the longshot") is the opposite profile:
   high confirmation ($2,781 of $9,831) and a 91% win rate, but each loss
   costs 15–20 wins. It lost on the confirmed subset (−$170). The worst
   trades were all NO on `hit_up` barriers in names that rallied:

   | bet | price | result | PnL | stock move 09-15→09-30 |
   |---|---:|---|---:|---|
   | MU hit_up 1020, monthly | 0.60 | touched | −$370 | MU +14.8% |
   | HOOD hit_up 117.5, wk 09-18 | 0.458 | touched | −$286 | HOOD (5% daily range) |
   | META hit_up 720, wk 09-25 | 0.34 | touched | −$212 | META +8.2% |
   | MU hit_up 960, monthly | 0.28 | touched | −$175 | MU +14.8% |

**Why YES lost.** The 65 YES bets predicted a 42% touch rate and got 29%. The
10-01 calibration table explains it: when the engine sits 3–10¢ *above*
the market mid, it says ~50% while the realised rate is ~27%. That band
supplies nearly every YES buy, and it is where the engine is most wrong. One
day did most of the damage:

- **09-28: 33 YES bets, $2,590, −$1,590.** Two sessions before the monthly
  deadline the model bought a basket of short-dated barriers on both sides
  (HOOD 125↑ and 110↓, PLTR 198↑ and 180↓, EWY 174↓ and 172↓, RKLB 76↑,
  SPCX 140↓, COIN 200↑…). The engine said 40% average touch probability;
  24% touched. SPY then fell −0.74%, −0.18% and −0.21%: nothing moved
  enough. Every one of these fairs carried the `single_expiry` flag. With no
  option expiry before 09-30, the engine priced a 2-day barrier off the
  10-02 weekly's vol, which still held the vol premium (PLTR σ 0.58 vs 25%
  realised). This is a known open question from the 09-18 review, now visible in results. Across all
  markets, though, `single_expiry` fairs are not worse calibrated than the
  rest (both overshoot by ~3¢), so it's the vol premium more than the flag.

**By ticker.** EWY +$1,383 (36 positions, the largest exposure). That's
uncomfortable, because FINDINGS §4 calls EWY's chain illiquid (27% of strikes
trade) and it was "pure artifact" on the first screen. GOOGL +$933, TSLA
+$708. HOOD −$453, RKLB −$354, PLTR −$340.

**Verdict.** The engine is genuinely better than the market mid at saying
which barriers *won't* be touched. It is no good at saying which *will*. The
money is in NO, but the profitable NO quotes were mostly not executable.
The confirmed +$411 is the honest number.

---

## factor_neutral: hedged, small and flat

**What it does.** It uses edge_taker's signal, sized greedily but kept within
±5% of bankroll in net beta-dollar-delta. It prefers YES pairs on both sides
of one ticker (a synthetic straddle), with tighter per-ticker (8%) and
per-direction (25%) caps.

**What it bought.** Only $2,359 in the whole month: the beta band rejects
most of what edge_taker takes. 35 NO ($1,787) and 34 YES ($572).

| reason | n | cost | PnL |
|---|---:|---:|---:|
| `edge_neutral` (single bets that fit the band) | 55 | $2,062 | +$284 |
| `straddle_pair` (both sides of a ticker) | 14 | $297 | −$297 |

- The NO legs won 97% of the time (+$242), mostly 93¢ NO earning 4–8%, plus
  seven cheap NO for +$112 that were never confirmed.
- **Every straddle lost.** RKLB 76↑ / 68↓ (−$141) and SPCX 160↑ / 135↓
  (−$100) were bought on 09-28 for the 09-30 deadline, the same mistake as
  edge_taker's 09-28 basket. A straddle is pure long vol, which is the wrong
  side of this month.
- The YES legs predicted 39% and touched 21%.

**Verdict.** It did what it was built to do: on its worst day it lost $1.1k
cumulative, against edge_taker's $3.3k. But at $2.4k deployed, nothing it
earns can matter. The straddle preference adds exactly the long-vol exposure
that lost in September. Without it, the model is a small, hedged edge_taker
that made about +$280.

---

## balanced_book: forced into the losing side

**What it does.** It uses edge_taker's signal under one rule: no quadrant (YES
hit_up, YES hit_down, NO hit_up, NO hit_down) may exceed the smallest
quadrant by more than $500 of max loss. The idea is that the four quadrants'
market risk cancels.

**What it bought.** 40 NO ($1,819, **+$428**) and 71 YES ($3,020,
**−$2,076**).

The rule is the problem. edge_taker's best NO bets keep growing the NO
quadrants, so the slack fills up, and to keep buying NO the model has to buy
YES as well, including YES it would never take on edge alone. YES touched 24%
of the time against a predicted 38%.

- **09-28 is almost the whole loss:** 37 YES bets, $2,446, −$2,026 (HOOD 125↑,
  PLTR 180↓ and 198↑, SPCX 140↓, RKLB 76↑, each −$144 to −$150). It is the same
  2-day monthly basket as edge_taker's, and balanced_book bought more of it
  because the quadrants were short of YES.
- Weeklies made +$442 and monthlies lost −$2,090.
- Best bets were the same NO as edge_taker (META 690↑ NO +$375, GOOGL 340↓ NO
  +$92), plus one lucky 5¢ YES (ABNB 152↓, +$190).

**Verdict.** The quadrant balance is the wrong hedge for this signal. It
neutralises direction, but it also neutralises the vol view, and the vol view
(sell it) was the only thing that paid. MODELS §5.2b warned that YES/NO is not
the risk axis. September showed the cost.

---

## empirical_longshot: the wrong shape, from the wrong history

**What it does.** It ignores options and uses the historical touch frequency
for the barrier's distance in sigmas and its horizon. There are two shapes:

- *Sell longshots:* buy NO when YES ≤ 12¢ and history says ≤ half that.
- *Buy the middle:* buy YES at 15–50¢ when history beats the cost by ≥ 4¢.

**What it bought.**

| shape | n | cost | PnL | win rate | predicted |
|---|---:|---:|---:|---:|---:|
| buy_middle (YES) | 139 | $9,309 | **−$5,732** | 19% | 40% |
| sell_longshot (NO) | 8 | $931 | +$63 | 100% | — |

So it is effectively a "buy mid-priced YES" model. By price: 24 YES bought at
10–20¢ all lost (−$1,443), and 75 at 20–35¢ lost −$3,244 (16% touched).
Worst names were PLTR −$1,030, EWY −$979 and AMZN −$766. It also bought YES
on the commodity and FX ladders (XAUUSD 4450↑, WTI 115↑, XAGUSD 71↑), where
nothing else trades. All of those lost.

**Why it failed.** The touch table is built from sessions *before*
2026-02-01, deliberately, to stay out of sample. Those years were far more
volatile than this September: 2025 realised vol was PLTR 67%, TSLA 63%,
SPY 19%, against September's 25%, 30% and 11%. The table measures distance
in recent-EWMA sigmas, which should adjust for this. But the shape of the
touch distribution from a trending, high-vol history (fat tails, momentum
days) still promised more touches than a calm, mean-reverting fortnight
produced.

P1 measured this same rule at +10.3% over Feb–Aug 2026. In September it lost
55%. This is the clearest regime failure of the five models.

The longshot NO side, which MODELS §5.3 designed as the main trade, fired 8
times: three filters (YES ≤ 12¢, history ≤ half the price, the option veto)
rarely agree.

**Verdict.** The model's own signal says to buy vol, and September punished
anyone long vol. Its "sell longshots" half, which is the trade that worked
everywhere else, was too hard to trigger to matter.

---

## random_control: what doing nothing clever costs

**What it does.** It picks random tradeable markets and random sides, with
the same sizing rules and roughly edge_taker's activity.

| side | n | cost | PnL | win rate |
|---|---:|---:|---:|---:|
| YES | 46 | $3,415 | −$2,300 (−67%) | 17% |
| NO | 48 | $10,677 | −$10 (−0.1%) | 79% |

This is the yardstick:

- A YES bought at random loses two-thirds. That is the spread, the fee, the
  favourite-longshot bias and the quiet month together. **A YES strategy has
  to beat −67%, not 0%.** factor_neutral's YES did (−45%), as did
  edge_taker's (−52%) and empirical_longshot's (−62%). balanced_book's (−69%)
  did not. None came close to zero.
- A NO bought at random breaks even. So edge_taker's NO profit (+$5.1k
  optimistic, +$270 confirmed) is genuinely above the random baseline. It is
  not just "NO won in a calm month".

---

## What September says, taken together

1. **The edge is on the NO side.** Every option-signal model's NO beat the
   random NO, and every model's YES lost heavily.
2. **The best-looking NO trades were mostly unfillable.** Cheap NO: $3,211
   optimistic, $376 confirmed. Capacity and quote persistence, not signal,
   bound the result (FINDINGS §3.3 and §7).
3. **Short-dated YES near a monthly deadline is the most expensive mistake**
   (09-28, all four signal models). These fairs come from a later expiry's
   vol on a 2-day horizon.
4. **The two hedged variants paid for their hedges.** balanced_book had to
   buy losing YES. factor_neutral's straddles were long vol in a calm month.
5. **A single calm month can't separate edge from vol premium.** October
   is the test. Through 10-08, edge_taker is again positive on October
   entries (+22% marked at today's ask, +5% confirmed), but most of that is
   two tickers.

## Fixes made 2026-10-09 that changed these numbers

1. **Dates priced before their 5-minute bars existed.** 09-23, 09-29 and
   10-01 were priced during collector outages with no bars, so the touch
   check saw "no evidence" and treated every barrier as untouched. There
   were 40 fills on barriers that had already printed, all on 09-29 and
   10-01. Repriced, and `daily.py` now defers any cycle the bars haven't
   reached yet.
2. **Same-day settlements were invisible.** `resolutions_upto` compared
   timestamps as text (`2026-10-08T13:48` sorted after `2026-10-08 19:45`),
   so a settlement on the cutoff's own date counted as future. During the
   replay this delayed cash by a day. At the end of a run, the last day's
   settlements were never booked.

September changed by: edge_taker +$3,381 → +$3,396, balanced_book −$1,982 →
−$1,647, empirical_longshot −$5,588 → −$5,669, factor_neutral −$121 → −$13,
random_control −$163 → −$2,310. The control moves most because its random
draws depend on the tradeable universe, and the fixes changed the universe.
