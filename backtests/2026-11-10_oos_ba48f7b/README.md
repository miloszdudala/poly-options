# Out-of-sample replay of the 1 October model (run 2026-11-10)

**Question.** How did `edge_taker` on Polymarket weekly hit markets perform
on data it had never seen?

**Answer.** The model as committed on 1 October 2026 (`ba48f7b`), replayed
without changes on every trading day from 2 October to 6 November 2026: **419
bets, $26,455 staked, +$2,828 profit (+10.7%)**, all settled. A random
control over the same markets lost 18.8%.

## Why this result is out-of-sample

A result counts as out-of-sample only if every bet in it was placed on data
collected after the model code and all its parameters were committed.

- The model is commit `ba48f7b`, timestamp 2026-10-01 22:13:14 +0800
  (14:13 UTC). The replay ran that commit's code in a separate git worktree.
  No line of it was edited. The scripts in `scripts/` only tell it which dates
  to replay, and narrow the markets it sees to weekly ones.
- The replay starts on 2 October. The earliest bet was filled at
  2026-10-02 14:00 UTC, almost a day after the commit.
- Every input a bet used (order books, option chains, stock prices) was
  collected by the 15-minute collector after that commit. Rules added after
  1 October (the 2c YES premium, the per-market fee schedule, pricing with ATM
  volatility only) are **not** in this replay.

Earlier reported figures (e.g. the 16 September - 30 October baseline) were
produced by code tuned on part of the same period, so they are not
out-of-sample. This one is.

## What the 1 October model does

- **Fair value:** the probability of a touch during regular trading hours
  only (Monte Carlo, overnight gaps unobserved). The probability is computed
  twice, once with the implied volatility at the barrier strike and once with
  the at-the-money volatility. The model takes whichever of the two is less
  favourable to the side being bought.
- **Option data:** Cboe's free chain, 15 minutes delayed, which is what would
  really be available when trading.
- **Threshold:** EV of at least 3c a share **and** a return of at least 8%;
  above a 90c ask, a return of at least 2%. There is no YES premium.
- **Fee:** 0.04 x min(p, 1-p) per share.
- **Fills:** walk the stored order book level by level, take at most half of
  each level, with a $1 minimum. A fill counts if the book had the size when
  it was read.
- **Account:** $10,000 bankroll, at most $500 per market on NO and $150 on
  YES. Cash never bound for weekly markets: a run that saw all hit markets
  produced exactly the same 419 weekly bets as a run that saw only weekly
  ones.
- **Held to resolution.** Outcomes come from Polymarket's own resolutions.

## Results

| | Bets | Staked | EV promised | Profit | Return |
|---|---:|---:|---:|---:|---:|
| **edge_taker** | **419** | **$26,455** | $5,373 | **+$2,828** | **+10.7%** |
| random control | 311 | $39,496 | - | -$7,429 | -18.8% |

The random control buys randomly chosen markets at the ask (plus 2c of
slippage) under the same caps, at the model's activity level. It loses partly
to spread and fees, so it shows the model's bets were not random; it is not a
benchmark for the size of the return.

By deadline week:

| Week ending | Bets | Staked | Profit | Return |
|---|---:|---:|---:|---:|
| 2 Oct | 27 | $2,158 | +$692 | +32.1% |
| 9 Oct | 80 | $6,009 | +$763 | +12.7% |
| 16 Oct | 58 | $2,108 | -$177 | -8.4% |
| 23 Oct | 92 | $6,010 | +$1,428 | +23.8% |
| 30 Oct | 86 | $5,880 | -$205 | -3.5% |
| 6 Nov | 76 | $4,290 | +$327 | +7.6% |

## Delayed vs real-time option data

The option chain is 15 minutes delayed. To see whether real-time data would
change the result, every cycle was re-priced with the **next** snapshot's
chain, whose prices date from about the book's own time. Spot prices and touch
checks are identical; only the volatility input changes. Each day's last cycle
has no next chain, so it is dropped from both sides (570 cycles each).

| | Bets | Staked | EV promised | Profit | Return |
|---|---:|---:|---:|---:|---:|
| Delayed 15 min | 411 | $26,083 | $4,848 | +$2,711 | +10.4% |
| Real-time (stand-in) | 472 | $38,087 | $11,175 | +$4,285 | +11.2% |

The fair value barely changes: the median absolute difference is 0.14c with
barrier-strike vol and 0.05c with ATM vol (90th percentile ~0.9c, 99th
~3.6c, over 139,742 market-cycles). Real-time data takes 105 extra bets
($9,114 staked, +$91 profit) and drops 44 (+$119). The stand-in slightly
favours real-time: late in the window the next chain was about a minute newer
than the book.

## Limitations

- Six weeks. Bets on correlated stocks win and lose together, so the
  effective sample is closer to six weeks than to 419 bets. Four weeks were
  up, two down.
- Concentration: EWY contributed +$1,603 of the $2,828.
- The model promised $5,373 and delivered 53% of it; its fair values were too
  optimistic.
- The replay assumes the book's size was available when it was read. A second
  read about 40 seconds later found 80-88% of those dollars still there.
- The 1 October code is not the current code. See "What the 1 October model
  does" for every rule it used.

## Files

- `bets/edge_taker_delayed_all_cycles.csv` - every settled bet behind the
  headline (419 rows).
- `bets/random_control_all_cycles.csv` - the control (311 rows).
- `bets/edge_taker_delayed_matched_cycles.csv`,
  `bets/edge_taker_realtime_matched_cycles.csv` - the two sides of the
  delayed vs real-time comparison.
- `scripts/` - the scripts that ran the replay and built these tables.

Each row has the Polymarket `market_id`, side, first fill time, shares, cost
including fee, the EV the model computed at entry, the resolution and the
profit. Outcomes can be checked against Polymarket's public market pages
without the data store.

## Reproducing

This needs the Parquet data store (`data/store/`, ~2 GB, not in git).

```bash
git worktree add --detach ../wt_ba48 ba48f7b
# data/store in the worktree: the store frozen as of the run (complete files
# only; poly_books and poly_books_recheck up to 2026-11-06)
D=2026-10-02,2026-10-05,...,2026-11-06          # every trading day in between
for d in ${D//,/ }; do python ../wt_ba48/model/run_fair.py --date $d; done
python scripts/oos_run.py ../wt_ba48 weekly $D   # edge_taker + random control
python scripts/rt_price.py ../wt_ba48 $D         # real-time stand-in pricing
python scripts/cmp_run.py ../wt_ba48 base $D
python scripts/cmp_run.py ../wt_ba48 rt $D
python scripts/analyse.py ../wt_ba48             # totals and weekly tables
```
