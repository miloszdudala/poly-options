# empirical_longshot — 2026-09-16 to 2026-09-22

Run `20260923T132440Z`, 83 priced session cycles over 4 day(s), 15.0s. Outcomes read through `2026-09-22 20:31:03+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $740,400.00 | 5,991 |
| allowed by the caps | $16,438.54 | |
| filled (optimistic) | $8,712.50 | 174 fills |
| filled (confirmed) | $2,603.89 | 54 fills |
| fees paid | $269.02 | |

Fill rate 1.2% of what was wanted. Confirmed is 29.9% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 4,042
- `no_averaging_down`: 1,862
- `book`: 56
- `already_other_side`: 5
- `ticker_cap`: 1

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 36 | 36 |
| cost | $2,712.50 | $176.63 |
| PnL | $-1,351.36 | $-76.63 |
| return on cost | -49.8% | -43.4% |

Win rate 27.8% on 36 resolved positions against a mean entry probability of 43.0%. Capital-days 5,830.8, PnL per capital-day $-0.23.

25% of the 36 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $6,000.00 | 65 positions |
| marked at model fair | $5,695.63 | PnL $-304.37 |
| marked at the last bid | $4,244.58 | PnL $-1,755.42 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.2, 0.35) | 13 | 0.302 | 0.077 | $1,105.32 | $-447.45 |
| [0.35, 0.5) | 12 | 0.421 | 0.250 | $934.21 | $-461.27 |
| [0.5, 0.65) | 10 | 0.554 | 0.500 | $639.38 | $-444.69 |
| [0.95, 1.0) | 1 | 0.979 | 1.000 | $33.59 | $2.05 |

## Exposure

Net beta-dollar-delta over 83 cycles: median $-29,203.18, range $-106,115.56 to $46,216.62; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,525.90 | $525.90 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | +1.13% |
| 2026-09-18 | 26 | $8,081.85 | - | $-1,666.80 | 31 | -0.12% |
| 2026-09-21 | 12 | $8,875.20 | $793.35 | $0.00 | 0 | +1.54% |
| 2026-09-22 | 25 | $8,344.27 | $-530.93 | $0.00 | 0 | -0.02% |

PnL on SPY's daily return: slope $32,778.69 per 100% SPY move, 3 days.

## Concentration

Shares of $8,712.50 deployed across 101 positions (cost, resolved and open together).

- largest ticker `PLTR` at 11.2%, across 23 tickers
- largest single position 5.7%
- largest direction `short_stock` at 54.6%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,009.95 |
| hit_down_YES | $3,936.22 |
| hit_up_NO | $817.97 |
| hit_up_YES | $2,948.36 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 148 | $6,884.58 | 31 |
| sell_longshot | 26 | $1,827.92 | 23 |
