# empirical_longshot — 2026-09-16 to 2026-10-07

Run `20261007T224707Z`, 296 priced session cycles over 15 day(s), 535.8s. Outcomes read through `2026-10-07 20:39:56+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $2,621,050.00 | 20,115 |
| allowed by the caps | $30,491.23 | |
| filled (optimistic) | $15,712.75 | 330 fills |
| filled (confirmed) | $3,773.96 | 95 fills |
| fees paid | $498.66 | |

Fill rate 0.6% of what was wanted. Confirmed is 24.0% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 12,095
- `no_averaging_down`: 4,883
- `cash`: 2,726
- `ticker_cap`: 230
- `book`: 103
- `already_other_side`: 31

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 148 | 148 |
| cost | $11,214.62 | $1,826.21 |
| PnL | $-5,501.87 | $-729.62 |
| return on cost | -49.1% | -40.0% |

Win rate 24.3% on 148 resolved positions against a mean entry probability of 43.3%. Capital-days 59,590.6, PnL per capital-day $-0.09.

19% of the 148 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $4,498.13 | 45 positions |
| marked at model fair | $4,251.02 | PnL $-247.11 |
| marked at the last bid | $2,961.23 | PnL $-1,536.91 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.1, 0.2) | 1 | 0.197 | 0.000 | $3.04 | $-3.04 |
| [0.2, 0.35) | 48 | 0.291 | 0.042 | $3,681.02 | $-2,423.16 |
| [0.35, 0.5) | 60 | 0.408 | 0.217 | $4,613.91 | $-1,820.89 |
| [0.5, 0.65) | 30 | 0.560 | 0.433 | $1,883.29 | $-1,165.72 |
| [0.65, 0.8) | 1 | 0.709 | 0.000 | $150.00 | $-150.00 |
| [0.95, 1.0) | 8 | 0.982 | 1.000 | $883.36 | $60.93 |

## Exposure

Net beta-dollar-delta over 296 cycles: median $-15,684.93, range $-106,115.56 to $46,213.11; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,525.90 | $525.90 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | +1.13% |
| 2026-09-18 | 26 | $8,081.85 | - | $-1,666.80 | 31 | -0.12% |
| 2026-09-21 | 12 | $8,875.20 | $793.35 | $0.00 | 0 | +1.54% |
| 2026-09-22 | 25 | $8,344.27 | $-530.93 | $142.25 | 3 | -0.02% |
| 2026-09-23 | 16 | $5,922.41 | $-2,421.86 | $783.72 | 5 | -0.72% |
| 2026-09-24 | 26 | $7,353.37 | $1,430.97 | $0.00 | 0 | -0.08% |
| 2026-09-25 | 22 | $6,097.71 | $-1,255.67 | $-2,050.39 | 28 | +0.54% |
| 2026-09-26 | 0 | - | - | $-70.72 | 1 | - |
| 2026-09-28 | 21 | $6,169.53 | - | $0.00 | 0 | -0.75% |
| 2026-09-29 | 2 | $5,394.45 | $-775.08 | $42.75 | 2 | -0.18% |
| 2026-09-30 | 21 | $3,789.29 | $-1,605.16 | $-3,083.86 | 54 | -0.21% |
| 2026-10-01 | 1 | $4,007.26 | $217.97 | $-408.16 | 4 | +0.18% |
| 2026-10-02 | 26 | $3,739.36 | $-267.90 | $493.90 | 15 | +0.74% |
| 2026-10-05 | 26 | $5,147.11 | $1,407.75 | $0.00 | 0 | +0.67% |
| 2026-10-06 | 26 | $4,957.24 | $-189.87 | $0.00 | 0 | +0.55% |
| 2026-10-07 | 26 | $4,251.02 | $-706.22 | $0.00 | 0 | -0.24% |

PnL on SPY's daily return: slope $87,518.46 per 100% SPY move, 13 days.

## Concentration

Shares of $15,712.75 deployed across 193 positions (cost, resolved and open together).

- largest ticker `PLTR` at 11.9%, across 23 tickers
- largest single position 3.2%
- largest direction `short_stock` at 50.6%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,820.15 |
| hit_down_YES | $6,847.73 |
| hit_up_NO | $1,110.53 |
| hit_up_YES | $5,934.34 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 289 | $12,782.07 | 60 |
| sell_longshot | 41 | $2,930.68 | 35 |
