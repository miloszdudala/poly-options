# empirical_longshot — 2026-09-16 to 2026-10-02

Run `20261005T021019Z`, 218 priced session cycles over 12 day(s), 313.9s. Outcomes read through `2026-10-02 20:50:11+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $1,781,050.00 | 13,598 |
| allowed by the caps | $27,593.03 | |
| filled (optimistic) | $14,073.46 | 295 fills |
| filled (confirmed) | $3,083.19 | 77 fills |
| fees paid | $454.48 | |

Fill rate 0.8% of what was wanted. Confirmed is 21.9% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 8,847
- `no_averaging_down`: 3,575
- `cash`: 800
- `ticker_cap`: 207
- `book`: 98
- `already_other_side`: 30

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 133 | 133 |
| cost | $10,069.23 | $1,755.18 |
| PnL | $-5,995.77 | $-658.58 |
| return on cost | -59.5% | -37.5% |

Win rate 22.6% on 133 resolved positions against a mean entry probability of 43.1%. Capital-days 56,968.8, PnL per capital-day $-0.11.

17% of the 133 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $4,004.23 | 43 positions |
| marked at model fair | $3,739.36 | PnL $-264.87 |
| marked at the last bid | $2,366.30 | PnL $-1,637.93 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.1, 0.2) | 1 | 0.197 | 0.000 | $3.04 | $-3.04 |
| [0.2, 0.35) | 45 | 0.291 | 0.044 | $3,499.03 | $-2,241.16 |
| [0.35, 0.5) | 54 | 0.407 | 0.185 | $3,989.25 | $-2,742.52 |
| [0.5, 0.65) | 24 | 0.560 | 0.417 | $1,544.56 | $-919.98 |
| [0.65, 0.8) | 1 | 0.709 | 0.000 | $150.00 | $-150.00 |
| [0.95, 1.0) | 8 | 0.982 | 1.000 | $883.36 | $60.93 |

## Exposure

Net beta-dollar-delta over 218 cycles: median $-17,622.79, range $-106,115.56 to $46,263.74; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,525.90 | $525.90 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | - |
| 2026-09-18 | 26 | $8,081.85 | - | $-1,666.80 | 31 | - |
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
| 2026-10-02 | 26 | $3,739.36 | $-267.90 | $0.00 | 0 | +0.74% |

PnL on SPY's daily return: slope $72,234.57 per 100% SPY move, 10 days.

## Concentration

Shares of $14,073.46 deployed across 176 positions (cost, resolved and open together).

- largest ticker `PLTR` at 12.0%, across 23 tickers
- largest single position 3.6%
- largest direction `short_stock` at 51.8%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,453.82 |
| hit_down_YES | $6,348.52 |
| hit_up_NO | $945.48 |
| hit_up_YES | $5,325.64 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 260 | $11,674.16 | 47 |
| sell_longshot | 35 | $2,399.30 | 30 |
