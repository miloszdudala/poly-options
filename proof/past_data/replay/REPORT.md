# Test 1: replay, 16 September to 13 November 2026

## Description

The model was replayed over the Polymarket order books and option prices collected every 15
minutes. At each snapshot it priced every weekly hit market and took one share of every bet worth at
least 3c a share more than its cost (fee included) and returning at least 8% (2% above a 90c
price). A bet counts only if the book showed that size at that moment. Every bet is held to
resolution and settled on Polymarket's own result. Pricing as of commit `4146d89`. A rerun on
20 November with today's code gave the same bets, cost and profit in every week.

Files in this folder: [`BETS.md`](BETS.md) (every bet, with the snapshot and the ask it was priced
on), [`SETTLEMENTS.md`](SETTLEMENTS.md) (how each market resolved), [`PERFORMANCE.md`](PERFORMANCE.md)
(one row per week, built by `proof/performance.py proof/past_data/replay` from the two files above). `BETS.md` also lists
the bets of the week of 20 November, without settlements; they are not counted here because the live
record took over on 18 November.

## Performance

| | |
|---|---:|
| Weeks | 9 |
| Bets (one share each) | 625 |
| Won / expected by the model | 327 / 334.7 |
| Cost | $297.42 |
| EV promised at entry | $37.33 |
| Profit | **$29.58 (9.9% of cost)** |
| Losing weeks | 1 of 9 |

Weekly rows are in [`PERFORMANCE.md`](PERFORMANCE.md).

## Random control

The same nine weeks with bets chosen at random instead of by the model: 625 one-share bets with the
same number per week, each at a random 15-minute snapshot (in session, from 10:00 ET, before the
deadline), on a random weekly hit market and a random side, bought at the stored best ask (at least
one share, ask 1c to 98c) with the venue fee, held to Polymarket's result, at most one bet per market.
Repeated 2,000 times.

| Control | Profit, average (95% range) | Runs that matched the model |
|---|---|---:|
| A. Random snapshot, market and side | -$54.83 (-$71.43 to -$37.91), -22% of cost; 8.9 of 9 weeks lose on average | 0 of 2,000 |
| B. As A, only live, tight books (YES ask + NO ask at most $1.05) | -$60.29 (-$74.55 to -$45.95) | 0 of 2,000 |
| C. The model's own snapshots and markets, side drawn at random | -$26.63 (-$46.68 to -$6.64) | 0 of 2,000 |
| **The model** | **+$29.58** | |

C uses the 604 of the 625 bets for which the book also showed the other side at that snapshot. On
those, the model's side made +$30.24 and the opposite side lost $83.50; buying both would have cost
about 9c a pair, the spread plus fee.

The random bets are not held to the model's gates (earnings weeks, barriers already touched), so part
of the loss in A and B may come from markets the model would not trade; C does not have this problem.
Nine weeks share one market, so this shows the model's choices are far from random, not how certain
its edge is in the future.

Back to the [summary](../REPORT.md).
