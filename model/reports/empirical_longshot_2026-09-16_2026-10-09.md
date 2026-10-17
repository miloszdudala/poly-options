# empirical_longshot — 2026-09-16 to 2026-10-09

Run `20261013T043353Z`, 343 priced session cycles over 17 day(s), 570.1s. Outcomes read through `2026-10-13 00:02:42+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $3,076,800.00 | 22,876 |
| allowed by the caps | $27,712.61 | |
| filled (optimistic) | $14,591.18 | 323 fills |
| filled (confirmed) | $3,189.09 | 79 fills |
| fees paid | $480.40 | |

Fill rate 0.5% of what was wanted. Confirmed is 21.9% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 8,790
- `cash`: 8,471
- `no_averaging_down`: 5,362
- `book`: 96
- `ticker_cap`: 70
- `already_other_side`: 47

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 162 | 162 |
| cost | $11,386.90 | $1,908.77 |
| PnL | $-6,795.71 | $-762.56 |
| return on cost | -59.7% | -40.0% |

Win rate 22.8% on 162 resolved positions against a mean entry probability of 42.9%. Capital-days 60,995.7, PnL per capital-day $-0.11.

18% of the 162 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $3,204.29 | 32 positions |
| marked at model fair | $3,089.31 | PnL $-114.98 |
| marked at the last bid | $2,330.88 | PnL $-873.40 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.2, 0.35) | 55 | 0.293 | 0.036 | $4,220.89 | $-2,963.03 |
| [0.35, 0.5) | 66 | 0.410 | 0.197 | $4,339.35 | $-2,851.07 |
| [0.5, 0.65) | 32 | 0.556 | 0.438 | $1,745.92 | $-894.79 |
| [0.65, 0.8) | 1 | 0.709 | 0.000 | $150.00 | $-150.00 |
| [0.95, 1.0) | 8 | 0.983 | 1.000 | $930.74 | $63.17 |

## Exposure

Net beta-dollar-delta over 343 cycles: median $0.00, range $-106,115.56 to $61,577.00; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,578.40 | $578.40 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | +1.13% |
| 2026-09-18 | 26 | $8,815.87 | - | $-1,800.46 | 36 | -0.12% |
| 2026-09-21 | 12 | $8,744.90 | $-70.97 | $0.00 | 0 | +1.54% |
| 2026-09-22 | 25 | $8,512.83 | $-232.08 | $143.64 | 4 | -0.02% |
| 2026-09-23 | 16 | $8,561.78 | $48.96 | $783.72 | 5 | -0.72% |
| 2026-09-24 | 26 | $7,435.80 | $-1,125.98 | $0.00 | 0 | -0.08% |
| 2026-09-25 | 22 | $6,627.45 | $-808.35 | $-1,951.37 | 31 | +0.54% |
| 2026-09-28 | 21 | $6,411.65 | $-215.80 | $139.66 | 1 | -0.75% |
| 2026-09-29 | 2 | $6,404.12 | $-7.52 | $144.21 | 3 | -0.18% |
| 2026-09-30 | 21 | $4,376.47 | $-2,027.65 | $-3,035.40 | 58 | -0.21% |
| 2026-10-01 | 1 | $5,183.42 | $806.95 | $-402.95 | 5 | +0.18% |
| 2026-10-02 | 26 | $3,620.61 | $-1,562.80 | $-1,132.20 | 14 | +0.74% |
| 2026-10-05 | 26 | $3,455.00 | $-165.62 | $0.00 | 0 | +0.67% |
| 2026-10-06 | 26 | $3,321.82 | $-133.17 | $0.00 | 0 | +0.55% |
| 2026-10-07 | 26 | $3,046.24 | $-275.58 | $0.00 | 0 | -0.24% |
| 2026-10-08 | 21 | $2,880.46 | $-165.79 | $0.00 | 0 | -0.42% |
| 2026-10-09 | 26 | $3,089.31 | $208.85 | $0.00 | 0 | +0.60% |

PnL on SPY's daily return: slope $-9,223.22 per 100% SPY move, 16 days.

## Concentration

Shares of $14,591.18 deployed across 194 positions (cost, resolved and open together).

- largest ticker `PLTR` at 12.0%, across 23 tickers
- largest single position 3.4%
- largest direction `short_stock` at 54.5%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,109.82 |
| hit_down_YES | $6,820.78 |
| hit_up_NO | $1,124.40 |
| hit_up_YES | $5,536.19 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 289 | $12,356.97 | 50 |
| sell_longshot | 34 | $2,234.22 | 29 |
