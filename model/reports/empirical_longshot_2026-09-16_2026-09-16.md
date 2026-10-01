# empirical_longshot — 2026-09-16 to 2026-09-16

Run `20260918T085702Z`, 21 priced session cycles over 1 day(s), 2.7s. Outcomes read through `2026-09-18 00:02:00+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $95,500.00 | 1,055 |
| allowed by the caps | $10,046.56 | |
| filled (optimistic) | $6,000.00 | 98 fills |
| filled (confirmed) | $2,464.43 | 51 fills |
| fees paid | $177.69 | |

Fill rate 6.3% of what was wanted. Confirmed is 41.1% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `no_averaging_down`: 626
- `direction_cap`: 376
- `book`: 33
- `ticker_cap`: 1

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 2 | 2 |
| cost | $161.48 | $0.00 |
| PnL | $182.03 | $0.00 |
| return on cost | 112.7% | - |

Win rate 100.0% on 2 resolved positions against a mean entry probability of 55.4%. Capital-days 163.5, PnL per capital-day $1.11.

**All 2 of those markets resolved Yes**, and 0 of the positions were on the NO side. That is selection, not skill or its absence: a hit market settles the instant it touches and otherwise waits for its deadline, so a window shorter than the markets' horizons resolves nothing but touches. This PnL measures which side of a touched market the model was on. It becomes a result when the deadlines pass and the markets that did not touch settle too.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $5,838.52 | 53 positions |
| marked at model fair | $6,082.30 | PnL $243.78 |
| marked at the last bid | $4,180.09 | PnL $-1,658.43 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.5, 0.65) | 2 | 0.554 | 1.000 | $161.48 | $182.03 |

## Exposure

Net beta-dollar-delta over 21 cycles: median $-107,946.10, range $-168,525.18 to $-86,901.01; largest absolute is 1685.3% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 21 | $10,304.68 | $304.68 | $11.52 | 1 | -0.44% |
| 2026-09-17 | 0 | - | - | $170.51 | 1 | - |

PnL regressed on SPY's daily return needs three days; the window has 2. Not computed.

## Concentration

Shares of $6,000.00 deployed across 55 positions (cost, resolved and open together).

- largest ticker `SPX` at 15.1%, across 19 tickers
- largest single position 8.3%
- largest direction `long_stock` at 50.0%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,030.73 |
| hit_down_YES | $2,556.50 |
| hit_up_NO | $443.50 |
| hit_up_YES | $1,969.27 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 73 | $4,525.76 | 28 |
| sell_longshot | 25 | $1,474.24 | 23 |
