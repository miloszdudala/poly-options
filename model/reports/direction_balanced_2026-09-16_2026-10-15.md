# direction_balanced — 2026-09-16 to 2026-10-15

Run `20261015T225329Z`, 418 priced session cycles over 21 day(s), 643.3s. Outcomes read through `2026-10-15 21:31:02.098527+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 100%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $101,664.21 | 321 |
| allowed by the caps | $97,388.85 | |
| filled (optimistic) | $19,592.22 | 417 fills |
| filled (confirmed) | $3,604.71 | 104 fills |
| fees paid | $377.27 | |

Fill rate 19.3% of what was wanted. Confirmed is 18.4% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `book`: 274
- `ticker_cap`: 22

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 282 | 282 |
| cost | $18,777.09 | $3,190.15 |
| PnL | $5,432.57 | $420.01 |
| return on cost | 28.9% | 13.2% |

Win rate 62.4% on 282 resolved positions against a mean entry probability of 61.0%. Capital-days 53,173.1, PnL per capital-day $0.10.

29% of the 282 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $815.13 | 37 positions |
| marked at model fair | $906.51 | PnL $91.38 |
| marked at the last bid | $826.96 | PnL $11.83 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.0, 0.05) | 1 | 0.047 | 0.000 | $98.06 | $-98.06 |
| [0.05, 0.1) | 2 | 0.079 | 0.500 | $11.96 | $100.05 |
| [0.1, 0.2) | 12 | 0.157 | 0.167 | $256.00 | $-173.74 |
| [0.2, 0.35) | 69 | 0.270 | 0.333 | $2,566.63 | $3,819.33 |
| [0.35, 0.5) | 40 | 0.431 | 0.325 | $1,955.62 | $117.71 |
| [0.5, 0.65) | 31 | 0.576 | 0.645 | $2,941.49 | $438.77 |
| [0.65, 0.8) | 19 | 0.723 | 0.737 | $1,100.03 | $368.93 |
| [0.8, 0.95) | 39 | 0.901 | 0.872 | $2,983.21 | $401.34 |
| [0.95, 1.0) | 69 | 0.974 | 1.000 | $6,864.09 | $458.24 |

## Exposure

Net beta-dollar-delta over 418 cycles: median $-1,248.55, range $-115,810.11 to $318,104.08; largest absolute is 3181.0% of bankroll.

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

PnL on SPY's daily return: slope $44,233.05 per 100% SPY move, 20 days.

## Concentration

Shares of $19,592.22 deployed across 319 positions (cost, resolved and open together).

- largest ticker `EWY` at 18.4%, across 20 tickers
- largest single position 2.6%
- largest direction `long_stock` at 50.5%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $8,805.58 |
| hit_down_YES | $1,286.43 |
| hit_up_NO | $8,403.42 |
| hit_up_YES | $1,096.79 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| direction_balanced | 417 | $19,592.22 | 104 |
