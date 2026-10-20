# direction_balanced — 2026-09-16 to 2026-10-19

Run `20261019T223735Z`, 469 priced session cycles over 23 day(s), 796.5s. Outcomes read through `2026-10-19 20:54:40.316086+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 100%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $67,856.86 | 387 |
| allowed by the caps | $65,473.25 | |
| filled (optimistic) | $18,035.47 | 488 fills |
| filled (confirmed) | $4,447.81 | 158 fills |
| fees paid | $368.29 | |

Fill rate 26.6% of what was wanted. Confirmed is 24.7% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `book`: 290
- `ticker_cap`: 16

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 339 | 339 |
| cost | $16,871.73 | $3,447.57 |
| PnL | $4,593.20 | $329.52 |
| return on cost | 27.2% | 9.6% |

Win rate 61.9% on 339 resolved positions against a mean entry probability of 61.4%. Capital-days 48,653.0, PnL per capital-day $0.09.

32% of the 339 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $1,163.74 | 46 positions |
| marked at model fair | $1,205.15 | PnL $41.41 |
| marked at the last bid | $1,108.72 | PnL $-55.02 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.0, 0.05) | 1 | 0.047 | 0.000 | $98.06 | $-98.06 |
| [0.05, 0.1) | 3 | 0.072 | 0.333 | $37.69 | $74.31 |
| [0.1, 0.2) | 13 | 0.158 | 0.154 | $354.71 | $-272.45 |
| [0.2, 0.35) | 77 | 0.271 | 0.325 | $2,427.09 | $3,348.30 |
| [0.35, 0.5) | 50 | 0.421 | 0.340 | $1,834.63 | $-493.89 |
| [0.5, 0.65) | 39 | 0.570 | 0.590 | $2,586.27 | $1,035.36 |
| [0.65, 0.8) | 27 | 0.728 | 0.704 | $1,280.31 | $225.18 |
| [0.8, 0.95) | 47 | 0.893 | 0.894 | $3,070.49 | $417.63 |
| [0.95, 1.0) | 82 | 0.974 | 0.988 | $5,182.47 | $356.82 |

## Exposure

Net beta-dollar-delta over 469 cycles: median $1,438.52, range $-195,515.77 to $328,985.72; largest absolute is 3289.9% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,117.54 | $117.54 | $-220.81 | 3 | -0.44% |
| 2026-09-17 | 0 | - | - | $-4.05 | 1 | +1.13% |
| 2026-09-18 | 26 | $10,943.98 | - | $1,774.18 | 22 | -0.12% |
| 2026-09-21 | 12 | $11,226.78 | $282.80 | $-331.47 | 4 | +1.54% |
| 2026-09-22 | 25 | $11,217.37 | $-9.40 | $-32.35 | 4 | -0.02% |
| 2026-09-23 | 16 | $9,132.49 | $-2,084.88 | $-106.35 | 4 | -0.72% |
| 2026-09-24 | 26 | $11,326.67 | $2,194.18 | $-56.78 | 1 | -0.08% |
| 2026-09-25 | 22 | $12,602.75 | $1,276.08 | $1,855.50 | 48 | +0.54% |
| 2026-09-28 | 21 | $13,632.19 | $1,029.44 | $-21.03 | 4 | -0.75% |
| 2026-09-29 | 2 | $13,416.60 | $-215.59 | $142.94 | 5 | -0.18% |
| 2026-09-30 | 21 | $14,011.49 | $594.89 | $12.04 | 78 | -0.21% |
| 2026-10-01 | 1 | $13,404.01 | $-607.48 | $1.13 | 3 | +0.18% |
| 2026-10-02 | 26 | $13,739.90 | $335.89 | $994.80 | 32 | +0.74% |
| 2026-10-05 | 26 | $14,090.43 | $350.53 | $0.00 | 0 | +0.67% |
| 2026-10-06 | 26 | $14,125.42 | $34.99 | $-175.20 | 7 | +0.55% |
| 2026-10-07 | 26 | $13,984.94 | $-140.48 | $-303.53 | 5 | -0.24% |
| 2026-10-08 | 21 | $13,981.52 | $-3.42 | $-346.39 | 7 | -0.42% |
| 2026-10-09 | 26 | $14,517.19 | $535.66 | $1,657.00 | 55 | +0.60% |
| 2026-10-12 | 1 | $14,594.04 | $76.86 | $5.01 | 1 | -0.06% |
| 2026-10-13 | 22 | $14,838.29 | $244.25 | $-3.95 | 1 | -0.01% |
| 2026-10-14 | 26 | $14,846.22 | $7.93 | $-1.06 | 2 | -1.09% |
| 2026-10-15 | 26 | $14,740.30 | $-105.93 | $-62.49 | 10 | +0.28% |
| 2026-10-16 | 25 | $14,423.93 | $-316.37 | $-205.36 | 38 | -1.11% |
| 2026-10-19 | 26 | $14,635.89 | $211.96 | $21.41 | 4 | +0.42% |

PnL on SPY's daily return: slope $30,885.47 per 100% SPY move, 22 days.

## Concentration

Shares of $18,035.47 deployed across 385 positions (cost, resolved and open together).

- largest ticker `EWY` at 18.4%, across 20 tickers
- largest single position 2.8%
- largest direction `long_stock` at 53.5%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $8,204.73 |
| hit_down_YES | $1,424.67 |
| hit_up_NO | $6,965.11 |
| hit_up_YES | $1,440.96 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| direction_balanced | 423 | $15,670.37 | 121 |
| direction_balanced_hedge | 65 | $2,365.10 | 37 |
