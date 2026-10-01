# empirical_longshot — 2026-09-16 to 2026-09-24

Run `20260924T211743Z`, 125 priced session cycles over 6 day(s), 152.8s. Outcomes read through `2026-09-24 20:19:45+00:00`.

Bankroll $10,000; per-market cap $150 YES / $500 NO; per-ticker 10%; per-direction 30%; take 50% of a level; fee 4% of min(p, 1-p).

## Capacity

| | dollars | count |
|---|---:|---:|
| orders proposed | $1,079,750.00 | 8,468 |
| allowed by the caps | $18,005.52 | |
| filled (optimistic) | $9,103.94 | 197 fills |
| filled (confirmed) | $2,720.60 | 64 fills |
| fees paid | $282.22 | |

Fill rate 0.8% of what was wanted. Confirmed is 29.9% of filled dollars; 0 fills had no next cycle to check against and are excluded from confirmed. Median fill depth_rank 0.

What stopped the rest, by order count:

- `direction_cap`: 5,881
- `no_averaging_down`: 2,376
- `ticker_cap`: 110
- `book`: 60
- `already_other_side`: 16

## Resolved PnL (the headline; MODELS 4.3 counts resolved only)

| | optimistic | confirmed |
|---|---:|---:|
| positions resolved | 44 | 44 |
| cost | $3,104.63 | $182.27 |
| PnL | $-425.39 | $-63.27 |
| return on cost | -13.7% | -34.7% |

Win rate 40.9% on 44 resolved positions against a mean entry probability of 43.3%. Capital-days 6,781.7, PnL per capital-day $-0.06.

39% of the 44 resolved markets resolved Yes.

## Open at the end, three ways (MODELS 4.3)

| | dollars | count |
|---|---:|---:|
| cost | $5,999.31 | 69 positions |
| marked at model fair | $3,778.07 | PnL $-2,221.24 |
| marked at the last bid | $2,679.55 | PnL $-3,319.76 |
| excluded | — | headline above |

## Predicted against realised, positions only

| entry win prob | n | predicted | realised | cost | PnL |
|---|---:|---:|---:|---:|---:|
| [0.2, 0.35) | 14 | 0.302 | 0.143 | $1,255.08 | $2.79 |
| [0.35, 0.5) | 17 | 0.424 | 0.471 | $1,163.22 | $-5.48 |
| [0.5, 0.65) | 12 | 0.551 | 0.583 | $652.75 | $-424.75 |
| [0.95, 1.0) | 1 | 0.979 | 1.000 | $33.59 | $2.05 |

## Exposure

Net beta-dollar-delta over 125 cycles: median $-22,550.84, range $-106,115.56 to $46,263.74; largest absolute is 1061.2% of bankroll.

| date | cycles | equity close (fair) | PnL marked | PnL settled | n settled | SPY |
|---|---:|---:|---:|---:|---:|---:|
| 2026-09-16 | 20 | $10,525.90 | $525.90 | $26.34 | 2 | -0.44% |
| 2026-09-17 | 0 | - | - | $289.09 | 3 | - |
| 2026-09-18 | 26 | $8,081.85 | - | $-1,666.80 | 31 | - |
| 2026-09-21 | 12 | $8,875.20 | $793.35 | $0.00 | 0 | +1.54% |
| 2026-09-22 | 25 | $8,344.27 | $-530.93 | $142.25 | 3 | -0.02% |
| 2026-09-23 | 16 | $5,922.41 | $-2,421.86 | $783.72 | 5 | -0.72% |
| 2026-09-24 | 26 | $7,353.37 | $1,430.97 | $0.00 | 0 | -0.08% |

PnL on SPY's daily return: slope $85,827.60 per 100% SPY move, 5 days.

## Concentration

Shares of $9,103.94 deployed across 113 positions (cost, resolved and open together).

- largest ticker `SPX` at 11.0%, across 23 tickers
- largest single position 5.5%
- largest direction `short_stock` at 55.4%

| quadrant (MODELS 5.2b) | dollars |
|---|---:|
| hit_down_NO | $1,050.95 |
| hit_down_YES | $4,215.97 |
| hit_up_NO | $829.46 |
| hit_up_YES | $3,007.55 |

## Fills by reason

| reason | fills | cost | confirmed fills |
|---|---:|---:|---:|
| buy_middle | 167 | $7,223.52 | 37 |
| sell_longshot | 30 | $1,880.42 | 27 |
