# direction_balanced — 2026-09-16 to 2026-10-08

Run `20261009T101141Z`, 317 priced session cycles over 16 day(s), 68.6s. Outcomes read through `2026-10-08 20:44:56+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 100%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $79,219.70 | 250 |
| allowed by the caps | $75,931.74 | |
| filled (optimistic) | $15,713.28 | 329 fills |
| filled (confirmed) | $3,049.37 | 80 fills |
| fees paid | $340.33 | |

Fill rate 19.8% of what was wanted. Confirmed is 19.4% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `book`: 211
- `ticker_cap`: 16

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 223 | 223 |
| cost | $14,447.52 | $2,814.02 |
| PnL | $3,503.75 | $170.88 |
| return on cost | 24.3% | 6.1% |

Win rate 57.8% on 223 resolved positions against a mean entry probability of 59.6%. Capital-days 47,792.8, PnL per capital-day $0.07.

30% of the 223 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $1,265.76 | 25 positions |
| marked at model fair | $2,044.26 | PnL $778.50 |
| marked at the last bid | $1,902.88 | PnL $637.12 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.0, 0.05) | 1 | 0.047 | 0.000 | $98.06 | $-98.06 |
| [0.05, 0.1) | 1 | 0.099 | 1.000 | $6.73 | $105.28 |
| [0.1, 0.2) | 12 | 0.157 | 0.167 | $256.00 | $-173.74 |
| [0.2, 0.35) | 57 | 0.269 | 0.298 | $2,163.73 | $2,785.23 |
| [0.35, 0.5) | 32 | 0.432 | 0.219 | $1,687.16 | $-164.85 |
| [0.5, 0.65) | 24 | 0.571 | 0.583 | $2,752.58 | $274.68 |
| [0.65, 0.8) | 14 | 0.718 | 0.714 | $749.79 | $266.17 |
| [0.8, 0.95) | 26 | 0.909 | 0.846 | $2,393.13 | $208.85 |
| [0.95, 1.0) | 56 | 0.972 | 1.000 | $4,340.34 | $300.19 |

## Exposure

Net beta-dollar-delta over 317 cycles: median $-2,475.35, range $-115,810.11 to $318,104.08; largest absolute is 3181.0% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,189.93 | $189.93 | $-235.98 | 3 | -0.44% |
| 2026-09-17 | 0 | - | - | $-174.72 | 1 | +1.13% |
| 2026-09-18 | 26 | $10,405.90 | - | $1,889.58 | 25 | -0.12% |
| 2026-09-21 | 12 | $11,219.66 | $813.76 | $-199.08 | 4 | +1.54% |
| 2026-09-22 | 25 | $11,144.21 | $-75.45 | $-29.10 | 4 | -0.02% |
| 2026-09-23 | 16 | $9,004.69 | $-2,139.52 | $-97.24 | 3 | -0.72% |
| 2026-09-24 | 26 | $11,346.91 | $2,342.23 | $-56.78 | 1 | -0.08% |
| 2026-09-25 | 22 | $12,801.62 | $1,454.70 | $1,975.51 | 46 | +0.54% |
| 2026-09-28 | 21 | $13,766.64 | $965.02 | $-21.03 | 4 | -0.75% |
| 2026-09-29 | 2 | $13,589.38 | $-177.26 | $142.94 | 5 | -0.18% |
| 2026-09-30 | 21 | $14,344.58 | $755.20 | $206.90 | 77 | -0.21% |
| 2026-10-01 | 1 | $13,935.65 | $-408.93 | $1.13 | 3 | +0.18% |
| 2026-10-02 | 26 | $14,509.33 | $573.68 | $1,215.88 | 30 | +0.74% |
| 2026-10-05 | 26 | $14,720.84 | $211.51 | $0.00 | 0 | +0.67% |
| 2026-10-06 | 26 | $14,814.11 | $93.26 | $-177.39 | 8 | +0.55% |
| 2026-10-07 | 26 | $14,370.92 | $-443.19 | $-597.85 | 4 | -0.24% |
| 2026-10-08 | 21 | $14,282.25 | $-88.67 | $-339.03 | 5 | -0.42% |

PnL on SPY's daily return: slope $51,735.79 per 100% SPY move, 15 days.

## Concentration

Shares of $15,713.28 deployed across 248 positions (cost, resolved and open together).

- largest ticker `EWY` at 16.6%, across 19 tickers
- largest single position 3.2%
- largest direction `short_stock` at 50.6%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $6,791.24 |
| hit_down_YES | $1,140.80 |
| hit_up_NO | $6,815.94 |
| hit_up_YES | $965.30 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| direction_balanced | 329 | $15,713.28 | 80 |
