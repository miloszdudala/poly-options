# empirical_longshot — 2026-09-16 to 2026-09-29

Run `20260929T213016Z`, 170 priced session cycles over 9 day(s), 252.0s. Outcomes read through `2026-09-29 14:10:47+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $1,394,000.00 | 10,893 |
| allowed by the caps | $22,206.89 | |
| filled (optimistic) | $11,141.01 | 238 fills |
| filled (confirmed) | $2,756.74 | 69 fills |
| fees paid | $360.45 | |

Fill rate 0.8% of what was wanted. Confirmed is 24.7% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 7,593
- `no_averaging_down`: 3,008
- `ticker_cap`: 155
- `book`: 76
- `already_other_side`: 29

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 68 | 68 |
| cost | $5,141.01 | $264.88 |
| PnL | $-2,186.20 | $-145.88 |
| return on cost | -42.5% | -55.1% |

Win rate 27.9% on 68 resolved positions against a mean entry probability of 42.4%. Capital-days 15,410.6, PnL per capital-day $-0.14.

26% of the 68 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $6,000.00 | 73 positions |
| marked at model fair | $3,514.70 | PnL $-2,485.30 |
| marked at the last bid | $2,844.14 | PnL $-3,155.86 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.2, 0.35) | 23 | 0.294 | 0.087 | $1,871.68 | $-613.82 |
| [0.35, 0.5) | 26 | 0.422 | 0.308 | $1,987.31 | $-829.58 |
| [0.5, 0.65) | 17 | 0.554 | 0.471 | $1,098.43 | $-594.85 |
| [0.65, 0.8) | 1 | 0.709 | 0.000 | $150.00 | $-150.00 |
| [0.95, 1.0) | 1 | 0.979 | 1.000 | $33.59 | $2.05 |

## Exposure

Net beta-dollar-delta over 170 cycles: median $-11,988.34, range $-106,115.56 to $46,286.48; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,525.90 | $525.90 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | +1.13% |
| 2026-09-18 | 26 | $8,081.85 | - | $-1,666.80 | 31 | -0.12% |
| 2026-09-21 | 12 | $8,875.20 | $793.35 | $0.00 | 0 | +1.54% |
| 2026-09-22 | 25 | $8,344.27 | $-530.93 | $142.25 | 3 | -0.02% |
| 2026-09-23 | 16 | $5,922.41 | $-2,421.86 | $783.72 | 5 | -0.72% |
| 2026-09-24 | 26 | $7,353.37 | $1,430.97 | $0.00 | 0 | -0.08% |
| 2026-09-25 | 22 | $6,097.71 | $-1,255.67 | $-1,690.09 | 23 | +0.54% |
| 2026-09-26 | 0 | - | - | $-70.72 | 1 | - |
| 2026-09-28 | 21 | $6,151.30 | - | $0.00 | 0 | -0.75% |
| 2026-09-29 | 2 | $5,328.50 | $-822.80 | $0.00 | 0 | - |

PnL on SPY's daily return: slope $65,594.63 per 100% SPY move, 6 days.

## Concentration

Shares of $11,141.01 deployed across 141 positions (cost, resolved and open together).

- largest ticker `PLTR` at 10.2%, across 23 tickers
- largest single position 4.5%
- largest direction `short_stock` at 55.9%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,050.95 |
| hit_down_YES | $5,399.84 |
| hit_up_NO | $832.41 |
| hit_up_YES | $3,857.82 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 207 | $9,257.66 | 41 |
| sell_longshot | 31 | $1,883.36 | 28 |
