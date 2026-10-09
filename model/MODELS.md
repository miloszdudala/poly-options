# The models: what each one does and how it has done

What every model shares (the fair-value engine, the fills, the accounting,
the data) is in `INSIGHTS.md` §1–4 and §6. Code comments citing "MODELS §3.x"
or "MODELS 4.x" mean those sections there. Model sections keep their old
numbers (5.x).

Last updated 2026-10-09, from the `run_all.py` run of that day after all of
its fixes.

> **Since 2026-10-09 every YES bought on the option signal pays a 2¢ risk
> premium.** In `edge_taker`, `factor_neutral`, `balanced_book`,
> `direction_balanced` and `scenario_hedged` (all through
> `sim.candidate_edges`), and in the `opportunities.py` sweep, a YES must clear
> the EV bar on `fair − 0.02`, not on `fair`. NO is not changed. The 2¢ is the
> volatility risk premium measured on 31 years of S&P 500 data, not fitted to
> ours; why it applies to YES and not to NO is in INSIGHTS §7.2.
> `empirical_longshot` does not use option prices, so it is unaffected. Every
> number below is with the premium.

## How to read the results

- **The bet.** Buy YES or NO at the ask, pay the fee once
  (`0.04 × min(p, 1−p)` per share), hold to resolution. EV = fair − price −
  fee on the side bought, **minus 2¢ on YES**. Bankroll $10,000; caps $150 per
  YES market and $500 per NO market unless a model says otherwise.
- **Optimistic vs confirmed.** Optimistic counts every fill the harness made
  against the book it saw. Confirmed counts only fills whose price level was
  still in the book at the next 15-minute snapshot. Neither is "closed
  positions": both cover every fill, open or settled. Confirmed is
  conservative. Most unconfirmed quotes were re-quoted by their poster, not
  taken by someone else, and some got cheaper (INSIGHTS §7.1).
- **September-deadline markets** (weeklies of 09-18 and 09-25, monthlies of
  09-30) have all resolved and are the clean sample. October markets have
  settled only where they touched, so their PnL so far leans YES. For them,
  open positions are valued at the **current ask**, i.e. what it would cost
  to buy the same position now.
- **Window:** 2026-09-16 to 2026-10-08, 16 trading days, one calm month
  (INSIGHTS §7.4). Nothing here is significant yet.

## Scoreboard

September-deadline markets, all resolved:

| model | positions | cost | PnL | ROI | confirmed cost | confirmed PnL |
|---|---:|---:|---:|---:|---:|---:|
| edge_taker | 180 | $15,652 | +$4,030 | +25.8% | $3,287 | +$257 |
| direction_balanced | 168 | $10,783 | +$3,483 | +32.3% | $2,452 | **+$350** |
| scenario_hedged | 162 | $11,826 | +$2,846 | +24.1% | $2,965 | +$90 |
| factor_neutral | 66 | $2,039 | −$107 | −5.2% | $394 | −$36 |
| balanced_book | 89 | $4,539 | −$1,277 | −28.1% | $688 | −$214 |
| empirical_longshot | 147 | $10,240 | −$5,669 | −55.4% | $1,838 | −$692 |
| random_control | 111 | $12,740 | −$1,270 | −10.0% | $4,449 | −$83 |

Positions opened in October, valued at payout if resolved and at the
current ask (10-08 close) if open:

| model | positions | cost | value now | PnL | ROI | confirmed PnL |
|---|---:|---:|---:|---:|---:|---:|
| edge_taker | 73 | $4,511 | $5,568 | +$1,057 | +23% | +$4 |
| scenario_hedged | 73 | $4,509 | $5,566 | +$1,057 | +23% | +$4 |
| direction_balanced | 57 | $3,778 | $4,651 | +$873 | +23% | −$22 |
| factor_neutral | 26 | $442 | $460 | +$18 | +4% | +$1 |
| balanced_book | 38 | $1,433 | $936 | −$497 | −35% | −$87 |
| empirical_longshot | 32 | $2,848 | $1,472 | −$1,376 | −48% | −$33 |
| random_control | 60 | $8,473 | $7,566 | −$908 | −11% | −$276 |

**What the premium changed (September-deadline markets).** Optimistic PnL rose
for every option model: edge_taker +$3,396 → +$4,030, direction_balanced
+$2,467 → +$3,483, scenario_hedged +$2,100 → +$2,846, balanced_book −$1,647
→ −$1,277. Confirmed PnL fell: +$411 → +$257, +$508 → +$350, +$292 → +$90.
The cause is a handful of YES bets that were both confirmed and winners; the
premium dropped them. Confirmed YES is a tiny sample ($160–440 of cost per
model), so this neither proves nor refutes the premium. October decides. The
random control changed only because its activity is set from edge_taker's.

Whole window, worst single day (marked): edge_taker −$3,457,
direction_balanced −$2,140, scenario_hedged −$2,196, empirical_longshot
−$2,028, balanced_book −$1,570, factor_neutral −$762, random_control
−$6,453.

**The pattern in every model:** NO bets make money and YES bets lose. A random
YES loses about two-thirds of its cost and a random NO breaks even, so that is
the bar each side has to clear.

---

## 5. The models

All share the option-implied signal (`sim.candidate_edges`): trade where
`min(fair_K, fair_atm)` beats price plus fee by EV ≥ 3¢ and ROI ≥ 8%,
**with YES paying the 2¢ risk premium on top**, never
`deep_otm`, chainless or unrepaired-earnings markets. empirical_longshot and
the control are the exceptions. They differ in which candidates they keep and
how they size them.

### 5.1 `edge_taker`: the baseline

**Does:** takes every candidate, best ROI first, to the per-market cap. No
portfolio constraint.

**Result:** the most profit, from NO bets. Before the premium: September NO
+$5,062, YES −$1,666. Most of the NO profit is cheap NO (bought under 50¢ when the market priced
a touch at 75–85%): +$3,850 on $3,211, of which only $376 confirmed. Its
largest single loss came from 09-28: 33 two-day YES bets on the September
monthlies, −$1,590. Largest exposure is EWY, whose option chain is thin.

**Verdict:** the signal is good on NO and bad on YES. The real question is
how much of the cheap NO could have been bought (INSIGHTS §7.1).

### 5.2 `factor_neutral`: hedged with deltas and betas

**Does:** keeps the book's net beta-dollar-delta (sensitivity to SPY) within
±5% of bankroll, per-direction cap 25%, per-ticker 8%, and a variance budget.
Prefers YES pairs on both sides of one ticker (a straddle).

**Result:** the band rejects most candidates, so only $2.3k was deployed in
September. Single bets made money and the straddles lost: they are long
volatility, and September was calm. Flat overall.

**Verdict:** too constrained to matter, and its straddle preference adds the
exposure that lost. Superseded by the two models below.

### 5.2b `balanced_book`: four quadrants in equal dollars

**Does:** no quadrant (YES hit_up, YES hit_down, NO hit_up, NO hit_down) may
exceed the smallest by more than $500 of cost.

**Result:** before the premium, NO +$428 and YES −$2,076 in September, with
09-28 alone −$2,026. With it, −$1,277 overall. To keep
buying NO it had to buy YES.

**Verdict:** equal dollars are not equal risk. $500 of NO at 93¢ earns $37 in
a calm week, $500 of YES at 30¢ loses $500. Kept for comparison only.

### 5.2c `direction_balanced`: up against down

**Does:** every bet says "the stock goes up" (YES hit_up, NO hit_down) or
"goes down" (YES hit_down, NO hit_up). The two sides may differ by at most
$500 of cost. Per-ticker cap 10%.

**Result:** best confirmed PnL in September (+$350 with the premium, +$508
without) and best ROI (+32%). Worst day a third smaller
than edge_taker's. It can balance with NO on both sides, so it hedges
direction without being pushed into YES.

**Verdict:** the best of the hedged models so far. It does not hedge a big
move: NO on both sides of a stock loses whichever way the stock jumps.

### 5.2d `scenario_hedged`: balance what positions pay

**Does:** eleven scenarios: every stock moves −3, −2, −1.5, −1, −0.5, 0, +0.5,
+1, +1.5, +2 or +3 of its own sigmas, all together, and 0 is "nothing moves".
Each position pays 1 or 0 per share in each scenario, from its distance to
the barrier `ln(B/S) / (sigma_atm·√T)`. An order is trimmed to the largest
size that keeps every scenario's book PnL above −$1,000. Per-ticker cap 12%.

**Result:** September +$2,846 (+$90 confirmed) with the premium, +$2,100
(+$292) without. It bought the same YES as
edge_taker and less NO, because a pile of NO is what loses in the big-move
scenarios. Smallest worst day of the profitable models.

**Verdict:** a working hedge that costs some profit, which is how a hedge
should behave. It doesn't model a path that touches both sides, and assumes
every stock moves together.

### 5.3 `empirical_longshot`: no options

**Does:** uses the historical touch frequency instead of option prices
(sessions before 2026-02-01, distance in EWMA sigmas). Two shapes:

- **sell longshots:** NO when YES ≤ 12¢ and history says ≤ half the price;
- **buy the middle:** YES at 15–50¢ when history beats cost by ≥ 4¢.

**Result:** the longshot shape fired 8 times (+$63). The middle shape made
139 YES bets, −$5,732: it predicted 40% and 19% touched. The history it learns
from (2021–2025) was far more volatile than September.

**Verdict:** worst model. It is a YES buyer, which is the side that loses.

### `random_control`

**Does:** random markets, random sides, the same sizing, edge_taker's
activity.

**Result:** YES −67%, NO −0.1% in September (run before the premium; the
control has no signal, so the premium does not apply to it). This is the yardstick for the
other models.

### 5.4 `maker`: not built

Resting bids at fair minus a margin would earn the spread and the 25% maker
rebate. But in a book this thin a resting bid is mostly filled by someone who
has just seen the stock move against it. Doing it honestly needs the trade
tape (now known to be reachable), a cancel rule and a queue model. Revisit if
the edge proves real but capacity-bound.

### 5.5 Not built

- Kalshi year-end index ladders against SPX options: 1–4¢ spreads, capacity
  unknown.
- Selling before resolution: round-trip cost 18–48%.
- Cross-venue arbitrage: no overlapping contracts.
