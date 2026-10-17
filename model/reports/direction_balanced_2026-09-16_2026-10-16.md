# direction_balanced — 2026-09-16 to 2026-10-16

Run `20261016T223220Z`, 443 priced session cycles over 22 day(s), 696.6s. Outcomes read through `2026-10-16 21:26:32+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 100%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $107,986.68 | 350 |
| allowed by the caps | $103,711.33 | |
| filled (optimistic) | $20,199.51 | 448 fills |
| filled (confirmed) | $3,858.60 | 116 fills |
| fees paid | $389.90 | |

Fill rate 18.7% of what was wanted. Confirmed is 19.1% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `book`: 295
- `ticker_cap`: 22

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 311 | 311 |
| cost | $19,573.69 | $3,457.67 |
| PnL | $5,428.86 | $398.50 |
| return on cost | 27.7% | 11.5% |

Win rate 63.0% on 311 resolved positions against a mean entry probability of 62.5%. Capital-days 54,377.9, PnL per capital-day $0.10.

31% of the 311 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $625.82 | 37 positions |
| marked at model fair | $736.35 | PnL $110.53 |
| marked at the last bid | $514.52 | PnL $-111.30 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.0, 0.05) | 1 | 0.047 | 0.000 | $98.06 | $-98.06 |
| [0.05, 0.1) | 2 | 0.079 | 0.500 | $11.96 | $100.05 |
| [0.1, 0.2) | 12 | 0.157 | 0.167 | $256.00 | $-173.74 |
| [0.2, 0.35) | 71 | 0.271 | 0.338 | $2,571.19 | $3,825.27 |
| [0.35, 0.5) | 41 | 0.430 | 0.341 | $1,975.98 | $164.85 |
| [0.5, 0.65) | 37 | 0.574 | 0.568 | $3,106.39 | $341.37 |
| [0.65, 0.8) | 24 | 0.729 | 0.708 | $1,226.53 | $377.80 |
| [0.8, 0.95) | 42 | 0.900 | 0.881 | $3,076.52 | $424.40 |
| [0.95, 1.0) | 81 | 0.974 | 0.988 | $7,251.05 | $466.92 |

## Exposure

Net beta-dollar-delta over 443 cycles: median $-1,105.54, range $-115,810.11 to $318,104.08; largest absolute is 3181.0% of bankroll.

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
| 2026-10-09 | 26 | $14,819.95 | $537.70 | $1,921.87 | 49 | +0.60% |
| 2026-10-12 | 1 | $15,300.42 | $480.47 | $5.01 | 1 | -0.06% |
| 2026-10-13 | 22 | $15,541.17 | $240.75 | $0.00 | 0 | -0.01% |
| 2026-10-14 | 26 | $15,568.15 | $26.98 | $-1.06 | 2 | -1.09% |
| 2026-10-15 | 26 | $15,523.95 | $-44.20 | $3.00 | 7 | +0.28% |
| 2026-10-16 | 25 | $15,421.15 | $-102.80 | $-3.71 | 29 | -1.11% |

PnL on SPY's daily return: slope $42,363.35 per 100% SPY move, 21 days.

## Concentration

Shares of $20,199.51 deployed across 348 positions (cost, resolved and open together).

- largest ticker `EWY` at 18.5%, across 20 tickers
- largest single position 2.5%
- largest direction `long_stock` at 50.3%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $9,008.18 |
| hit_down_YES | $1,363.14 |
| hit_up_NO | $8,679.52 |
| hit_up_YES | $1,148.66 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| direction_balanced | 448 | $20,199.51 | 116 |
