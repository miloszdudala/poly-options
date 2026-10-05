# empirical_longshot — 2026-09-16 to 2026-10-01

Run `20261001T221338Z`, 192 priced session cycles over 11 day(s), 335.6s. Outcomes read through `2026-10-01 14:13:58.021696+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $1,630,000.00 | 12,394 |
| allowed by the caps | $26,539.43 | |
| filled (optimistic) | $13,531.62 | 286 fills |
| filled (confirmed) | $2,859.51 | 74 fills |
| fees paid | $439.22 | |

Fill rate 0.8% of what was wanted. Confirmed is 21.1% of filled dollars; 40 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 8,847
- `no_averaging_down`: 3,175
- `ticker_cap`: 177
- `book`: 93
- `cash`: 32
- `already_other_side`: 30

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 129 | 129 |
| cost | $9,119.23 | $966.66 |
| PnL | $-5,587.60 | $-411.90 |
| return on cost | -61.3% | -42.6% |

Win rate 22.5% on 129 resolved positions against a mean entry probability of 42.5%. Capital-days 43,001.5, PnL per capital-day $-0.13.

17% of the 129 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $4,412.40 | 40 positions |
| marked at model fair | $4,007.26 | PnL $-405.14 |
| marked at the last bid | $2,545.47 | PnL $-1,866.93 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.1, 0.2) | 1 | 0.197 | 0.000 | $3.04 | $-3.04 |
| [0.2, 0.35) | 44 | 0.292 | 0.045 | $3,349.03 | $-2,091.16 |
| [0.35, 0.5) | 54 | 0.407 | 0.185 | $3,989.25 | $-2,742.52 |
| [0.5, 0.65) | 22 | 0.557 | 0.455 | $1,244.56 | $-619.98 |
| [0.65, 0.8) | 1 | 0.709 | 0.000 | $150.00 | $-150.00 |
| [0.95, 1.0) | 7 | 0.985 | 1.000 | $383.36 | $19.09 |

## Exposure

Net beta-dollar-delta over 192 cycles: median $-22,484.57, range $-106,115.56 to $46,340.25; largest absolute is 1061.2% of bankroll.

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
| 2026-10-01 | 1 | $4,007.26 | $217.97 | $0.00 | 0 | - |

PnL on SPY's daily return: slope $76,393.88 per 100% SPY move, 8 days.

## Concentration

Shares of $13,531.62 deployed across 169 positions (cost, resolved and open together).

- largest ticker `PLTR` at 11.8%, across 23 tickers
- largest single position 3.7%
- largest direction `short_stock` at 53.2%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,300.22 |
| hit_down_YES | $6,255.87 |
| hit_up_NO | $945.48 |
| hit_up_YES | $5,030.05 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 252 | $11,285.92 | 46 |
| sell_longshot | 34 | $2,245.70 | 28 |
