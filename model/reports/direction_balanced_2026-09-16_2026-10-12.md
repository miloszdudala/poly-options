# direction_balanced — 2026-09-16 to 2026-10-12

Run `20261014T023610Z`, 344 priced session cycles over 18 day(s), 376.7s. Outcomes read through `2026-10-13 21:55:28+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 100%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $92,450.25 | 285 |
| allowed by the caps | $88,174.90 | |
| filled (optimistic) | $18,700.97 | 377 fills |
| filled (confirmed) | $3,199.40 | 85 fills |
| fees paid | $363.80 | |

Fill rate 20.2% of what was wanted. Confirmed is 17.1% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `book`: 238
- `ticker_cap`: 22

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 273 | 273 |
| cost | $18,580.76 | $3,110.14 |
| PnL | $5,430.63 | $331.76 |
| return on cost | 29.2% | 10.7% |

Win rate 62.6% on 273 resolved positions against a mean entry probability of 61.2%. Capital-days 52,903.0, PnL per capital-day $0.10.

27% of the 273 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $120.21 | 10 positions |
| marked at model fair | $0.00 | PnL $-120.21 |
| marked at the last bid | $95.18 | PnL $-25.03 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.0, 0.05) | 1 | 0.047 | 0.000 | $98.06 | $-98.06 |
| [0.05, 0.1) | 1 | 0.099 | 1.000 | $6.73 | $105.28 |
| [0.1, 0.2) | 12 | 0.157 | 0.167 | $256.00 | $-173.74 |
| [0.2, 0.35) | 68 | 0.270 | 0.338 | $2,562.88 | $3,823.08 |
| [0.35, 0.5) | 37 | 0.430 | 0.297 | $1,889.22 | $120.84 |
| [0.5, 0.65) | 30 | 0.576 | 0.633 | $2,889.07 | $386.19 |
| [0.65, 0.8) | 18 | 0.722 | 0.778 | $1,056.30 | $412.65 |
| [0.8, 0.95) | 38 | 0.903 | 0.868 | $2,975.90 | $398.65 |
| [0.95, 1.0) | 68 | 0.974 | 1.000 | $6,846.59 | $455.75 |

## Exposure

Net beta-dollar-delta over 344 cycles: median $-1,825.72, range $-115,810.11 to $318,104.08; largest absolute is 3181.0% of bankroll.

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

PnL on SPY's daily return: slope $50,935.49 per 100% SPY move, 17 days.

## Concentration

Shares of $18,700.97 deployed across 283 positions (cost, resolved and open together).

- largest ticker `EWY` at 17.5%, across 20 tickers
- largest single position 2.7%
- largest direction `long_stock` at 51.8%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $8,676.33 |
| hit_down_YES | $1,140.80 |
| hit_up_NO | $7,872.06 |
| hit_up_YES | $1,011.78 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| direction_balanced | 377 | $18,700.97 | 85 |
