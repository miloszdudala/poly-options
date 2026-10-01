# empirical_longshot — 2026-09-16 to 2026-09-30

Run `20261001T070928Z`, 191 priced session cycles over 10 day(s), 48.1s. Outcomes read through `2026-10-01 06:48:27+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $1,620,850.00 | 12,336 |
| allowed by the caps | $22,888.20 | |
| filled (optimistic) | $11,533.06 | 246 fills |
| filled (confirmed) | $2,859.51 | 74 fills |
| fees paid | $375.53 | |

Fill rate 0.7% of what was wanted. Confirmed is 24.8% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 8,847
- `no_averaging_down`: 3,171
- `ticker_cap`: 176
- `book`: 79
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
| cost | $2,413.83 | 17 positions |
| marked at model fair | $1,865.62 | PnL $-548.22 |
| marked at the last bid | $1,659.15 | PnL $-754.68 |
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

Net beta-dollar-delta over 191 cycles: median $-22,638.50, range $-106,115.56 to $46,227.36; largest absolute is 1061.2% of bankroll.

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

PnL on SPY's daily return: slope $76,393.88 per 100% SPY move, 8 days.

## Concentration

Shares of $11,533.06 deployed across 146 positions (cost, resolved and open together).

- largest ticker `PLTR` at 11.3%, across 23 tickers
- largest single position 4.3%
- largest direction `short_stock` at 57.2%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,050.95 |
| hit_down_YES | $5,763.03 |
| hit_up_NO | $832.41 |
| hit_up_YES | $3,886.67 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 215 | $9,649.70 | 46 |
| sell_longshot | 31 | $1,883.36 | 28 |
