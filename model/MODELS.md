# The models: what each one does and how it has done

What every model shares (the fair-value engine, the fills, the accounting,
the data) is in `INSIGHTS.md` §1–4 and §6. Code comments citing "MODELS §3.x"
or "MODELS 4.x" mean those sections there. Model sections keep their old
numbers (5.x).

Last updated 2026-10-09, from the `run_all.py` run of that day after all of
its fixes.

> **Since 2026-10-09 every YES bought on the option signal pays a 2¢ risk
> premium.** In `edge_taker`, `direction_balanced` and `scenario_hedged` (all through
> `sim.candidate_edges`), and in the `opportunities.py` sweep, a YES must clear
> the EV bar on `fair − 0.02`, not on `fair`. NO is not changed. The 2¢ is the
> volatility risk premium measured on 31 years of S&P 500 data, not fitted to
> ours; why it applies to YES and not to NO is in INSIGHTS §7.2. Every
> number below is with the premium.

## How to read the results

- **The bet.** Buy YES or NO at the ask, pay the fee once
  (`0.04 × min(p, 1−p)` per share), hold to resolution. EV = fair − price −
  fee on the side bought, **minus 2¢ on YES**. Bankroll $10,000; caps $150 per
  YES market and $500 per NO market unless a model says otherwise.
- **Optimistic vs confirmed.** Optimistic counts every fill the harness made
  against the book it saw. Confirmed counts only fills a live trader could
  still have made after deciding. From 2026-10-16 17:00Z that means the bet
  was still buyable, in full, under its limit at the ~40 s recheck of the same
  books (`sim.confirm_by_recheck`); on 10-16 that passed 93% of cost, against
  25% under the old rule on the same fills. Before the recheck existed it
  means the exact price level was still there at the next 15-minute snapshot. Neither is "closed
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
| random_control | 111 | $12,740 | −$1,270 | −10.0% | $4,449 | −$83 |

Positions opened in October, valued at payout if resolved and at the
current ask (10-08 close) if open:

| model | positions | cost | value now | PnL | ROI | confirmed PnL |
|---|---:|---:|---:|---:|---:|---:|
| edge_taker | 73 | $4,511 | $5,568 | +$1,057 | +23% | +$4 |
| scenario_hedged | 73 | $4,509 | $5,566 | +$1,057 | +23% | +$4 |
| direction_balanced | 57 | $3,778 | $4,651 | +$873 | +23% | −$22 |
| random_control | 60 | $8,473 | $7,566 | −$908 | −11% | −$276 |

**What the premium changed (September-deadline markets).** Optimistic PnL rose
for every option model: edge_taker +$3,396 → +$4,030, direction_balanced
+$2,467 → +$3,483, scenario_hedged +$2,100 → +$2,846. Confirmed PnL fell: +$411 → +$257, +$508 → +$350, +$292 → +$90.
The cause is a handful of YES bets that were both confirmed and winners; the
premium dropped them. Confirmed YES is a tiny sample ($160–440 of cost per
model), so this neither proves nor refutes the premium. October decides. The
random control changed only because its activity is set from edge_taker's.

Whole window, worst single day (marked): edge_taker −$3,457,
direction_balanced −$2,140, scenario_hedged −$2,196, random_control
−$6,453.

**The pattern in every model:** NO bets make money and YES bets lose. A random
YES loses about two-thirds of its cost and a random NO breaks even, so that is
the bar each side has to clear.

---

## 5. The models

All share the option-implied signal (`sim.candidate_edges`): trade where
`min(fair_K, fair_atm)` beats price plus fee by EV ≥ 3¢ and ROI ≥ 8%,
**with YES paying the 2¢ risk premium on top**, never
`deep_otm`, chainless or unrepaired-earnings markets. The control is the
exception. They differ in which candidates they keep and
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

### Removed 2026-10-17

Three models were dropped as not making sense, with their code and tests
(git history has both). Their September results stay in the old reports.

- **5.2 `factor_neutral`** (beta-dollar-delta band): rejected most
  candidates, and its straddle preference added the long-vol exposure that
  lost in a calm month. Flat.
- **5.2b `balanced_book`** (four quadrants in equal dollars): equal dollars
  are not equal risk, so to keep buying NO it had to buy YES. −28%.
- **5.3 `empirical_longshot`** (historical touch rates instead of options):
  learned from a far more volatile 2021–2025; its YES "middle" bets lost
  −$5,732. −55%.

### 5.2c `direction_balanced`: up against down

**Does:** every bet says "the stock goes up" (YES hit_up, NO hit_down) or
"goes down" (YES hit_down, NO hit_up). The two sides may differ by at most
10% of the book's cost ($150 while the book is small). While the book is past
that, a bet on the light side may come in at EV >= 0 instead of 3c, up to
what levels the two sides. Per-ticker cap 10%.

**Changed 2026-10-19.** The limit was a fixed $500, a sixth of the ~$3k
book these markets allow, so it almost never applied: 99% of its money was in
edge_taker's positions. Making it relative but only trimming orders kept
the median imbalance at 11%, yet one side resolving left the worst 5% of
cycles 64% one-sided. Hedge legs bring that to 7% median, 22% worst 5%.

**Result:** best confirmed PnL in September (+$350 with the premium, +$508
without) and best ROI (+32%). Worst day a third smaller
than edge_taker's. It can balance with NO on both sides, so it hedges
direction without being pushed into YES.

**Verdict:** the best of the hedged models so far. It does not hedge a big
move: NO on both sides of a stock loses whichever way the stock jumps.

### 5.2d `scenario_hedged`: limit the book's risk, measured the way it is

**Does:** prices every bet in 49 scenarios: a volatility multiplier `k`
(moves are k times what options priced; log-sd 0.21) times a market drift
`mu` (sd 0.39 implied sigmas), each on 7 points out to ±3 sd, weighted by a
normal density. A bet's win probability in a scenario comes from its own
model probability: `q = 2N(−a)` gives the barrier distance `a`, and the touch
probability becomes `2N(−(a ∓ mu)/k)`. Two limits, relative to the cost
held: the book's **standard deviation ≤ 8%** (variance across scenarios, plus
each ticker × type × side's coin flip with the bets inside it perfectly
correlated), and **no scenario's expected PnL below −10%** ($50 floors while
the book is small). A book that drifted out may not drift further; while out,
a bet that brings it back may come in at EV ≥ 0. Per-ticker cap 12%.

**Why it was rebuilt (2026-10-19).** The old version moved every stock the
same number of sigmas in one direction, with a −$1,000 worst-case limit. It
made +81% in the week of 09-21 and lost in another, because it measured the
wrong risk:
- **Volatility is the common factor, not direction.** Across all markets,
  touches came in at 0.72× the model's rate in the week of 09-21 and 1.59× in
  the week of 10-12 (k = 0.77 to 1.33). A calm week pays every NO at once,
  above and below the price. The old grid treated NO above + NO below as a
  hedge of each other.
- **Same-ticker bets are one bet.** EWY hit_up NO at 188/192/193/194 win or
  lose together. Counted that way, every week of edge_taker's and this
  model's ROI is within ±1.6 sd of what the bets implied; counted as
  independent, 09-21 was +2.8 sd. So the swings were the real risk of the
  bets, not a bookkeeping error.
- **Cheap NO is a lopsided coin.** edge_taker's average NO cost 36c: −100% or
  +180%. A worst-case limit does not see that spread at all.
A limit on the range (best minus worst scenario) was tried and dropped: one
cheap NO used the whole range. A limit on independent variance alone was
tried and dropped: 09-21 still came out 2.7 sd high.

**Result (09-16 .. 10-16, deadline-passed markets, ROI by week of entry):**

| sd limit | bet | settled PnL | weekly ROI | each week vs its own risk |
|---|---:|---:|---|---|
| none (edge_taker) | $27.7k | +$5,984 | +24 +48 +6 +14 −11 | −1.3 .. +1.6 sd |
| 25% | $16.1k | +$2,204 | +5 +50 −2 +10 +12 | −0.8 .. +2.1 sd |
| 15% | $10.2k | +$1,346 | +6 +33 +4 +7 −50 | −1.9 .. +1.7 sd |
| **8% (default)** | $3.3k | +$246 | **+6 +17 +13 +8 +6** | **−0.2 .. +0.2 sd** |

**Verdict:** at 8% it is balanced: no week far from +10%, every week as the
model's own probabilities said. The price is size. The edge is mostly in
cheap NO clustered on a few tickers, which is what the limit cuts, and the
hedge that would let size back in, a YES that pays in a wild week, rarely
clears EV ≥ 0: hedge legs were 21% of its spend and mostly NO elsewhere, not
YES. Five weeks, one of them calm, fitted the factor sizes too; re-fit them
on a longer history before trusting the 8%.

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
