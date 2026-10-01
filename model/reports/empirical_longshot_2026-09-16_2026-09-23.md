# empirical_longshot — 2026-09-16 to 2026-09-23

Run `20260924T002012Z`, 99 priced session cycles over 5 day(s), 6308.4s. Outcomes read through `2026-09-23 20:18:58+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $882,200.00 | 6,959 |
| allowed by the caps | $16,523.89 | |
| filled (optimistic) | $8,797.85 | 179 fills |
| filled (confirmed) | $2,645.94 | 58 fills |
| fees paid | $270.86 | |

Fill rate 1.0% of what was wanted. Confirmed is 30.1% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 4,803
- `no_averaging_down`: 2,020
- `book`: 56
- `ticker_cap`: 50
- `already_other_side`: 5

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 39 | 39 |
| cost | $2,797.85 | $176.63 |
| PnL | $-1,209.11 | $-76.63 |
| return on cost | -43.2% | -43.4% |

Win rate 33.3% on 39 resolved positions against a mean entry probability of 43.4%. Capital-days 5,978.0, PnL per capital-day $-0.20.

31% of the 39 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $6,000.00 | 64 positions |
| marked at model fair | $3,131.52 | PnL $-2,868.48 |
| marked at the last bid | $3,438.06 | PnL $-2,561.94 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.2, 0.35) | 13 | 0.302 | 0.077 | $1,105.32 | $-447.45 |
| [0.35, 0.5) | 14 | 0.424 | 0.357 | $1,015.75 | $-323.01 |
| [0.5, 0.65) | 11 | 0.552 | 0.545 | $643.20 | $-440.70 |
| [0.95, 1.0) | 1 | 0.979 | 1.000 | $33.59 | $2.05 |

## Exposure

Net beta-dollar-delta over 99 cycles: median $-24,251.34, range $-106,115.56 to $46,216.76; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,525.90 | $525.90 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | +1.13% |
| 2026-09-18 | 26 | $8,081.85 | - | $-1,666.80 | 31 | -0.12% |
| 2026-09-21 | 12 | $8,875.20 | $793.35 | $0.00 | 0 | +1.54% |
| 2026-09-22 | 25 | $8,344.27 | $-530.93 | $142.25 | 3 | -0.02% |
| 2026-09-23 | 16 | $5,922.41 | $-2,421.86 | $0.00 | 0 | - |

PnL on SPY's daily return: slope $32,778.69 per 100% SPY move, 3 days.

## Concentration

Shares of $8,797.85 deployed across 103 positions (cost, resolved and open together).

- largest ticker `SPX` at 11.4%, across 23 tickers
- largest single position 5.7%
- largest direction `short_stock` at 54.0%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,050.95 |
| hit_down_YES | $3,936.22 |
| hit_up_NO | $817.97 |
| hit_up_YES | $2,992.71 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 150 | $6,928.92 | 32 |
| sell_longshot | 29 | $1,868.93 | 26 |
