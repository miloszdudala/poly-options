# Past data: does the model work?

Three tests of the model that prices Polymarket's weekly "hit" markets (will a stock touch a price
before Friday's close?). The model is the MVP: the touch probability over regular trading hours only,
with the implied volatility of the listed option at the barrier strike.

Tests 2 and 3 do not use Polymarket at all: they check the pricing engine against what prices
actually did, 2018-2026. Test 1 checks the trading on top of it. Each test has its own report
(description, then results per sample) next to its data; follow the links in the table.

The live record, bets written down before they resolve, continues in [`proof/live_data/`](../live_data/) from
18 November 2026.

## Results

| Test | What it checks | Result |
|---|---|---|
| [1. Replay](replay/REPORT.md), 9 weeks of Polymarket books | The whole strategy: pricing, bar, fills, settlement | 625 bets, profit **$29.58 on $297.42 (9.9%)**, EV promised $37.33, 1 losing week of 9. Random bets in the same weeks: -$54.83 on average, none of 2,000 runs matched the model |
| [2. Calibration](historical/calibration/REPORT.md), 4,217 barriers, 2018-2026 | Does the touch probability match how often prices touched? | Predicted **1,072** touches, **1,077** happened: d = +0.1 points (95% interval -1.1 to +1.3); all 10 samples contain 0 |
| [3. PIT](historical/pit/REPORT.md), 8,478 observations, 2018-2026 | Does the model expect moves of the right size, over every priceable barrier? | mu = -0.013 (ideal 0, interval -0.031 to +0.005), s = 0.977 (ideal 1, interval 0.951 to 1.002) |

The 10 samples of tests 2 and 3 share the same weeks: the calibration samples are practically
independent, the PIT samples are worth about four independent ones; see
[The samples are correlated](#the-samples-are-correlated). The "All 10" rows account for it.

---

## The samples are correlated

The 10 samples of each test are disjoint (no point appears twice), but they all cover the same weeks
of 2018-2026, so they share the same market moves: a week of large moves pushes every sample the same
way. That shared week is the only link between them, and it was measured in two ways:

- **One-way ANOVA by week.** The variance of the per-point result (touched minus predicted for
  calibration; the normal score for PIT) is split into a part shared by points of the same week and
  the rest. The shared part, the intraclass correlation (ICC), is the correlation between two samples
  that each hold one point of the same weeks.
- **Resampling the same weeks for all samples at once** (the week bootstrap used for the intervals).

| | ICC by ANOVA (95% interval) | F test for a week effect | Correlation by resampling (range over pairs) | The 10 samples are worth |
|---|---|---|---|---:|
| Calibration | +0.012 (-0.002 to +0.030) | p = 0.05 | +0.01 (-0.10 to +0.12) | about 9 independent ones |
| PIT | +0.163 (+0.136 to +0.194) | p < 10^-60 | +0.13 (+0.05 to +0.25) | about 4 independent ones |

"Worth" is 10 / (1 + 9 x correlation), using the ICC. The calibration samples are practically
independent: whether one barrier is touched depends little on the week. The PIT samples are not: PIT
uses how far the price actually went, and in a week of large moves every stock goes further. Ten
agreeing PIT samples are therefore about four independent confirmations, not ten.

The "All 10" rows resample whole weeks, so their intervals already account for this correlation.

## Limitations

- Weekly hit markets on single stocks and ETFs only. No index, no monthly markets.
- No volume in the historical data, so a spread filter (spread at most 50% of the mid) stands in
  for the live rule that a strike must trade at least 5% of the busiest strike's volume.
- Earnings dates come from Yahoo Finance (four a year per company over the whole period).
- The samples were drawn from the data downloaded by 21 November 2026. For weeks after November
  2023 that was about three moments per week, against more than a hundred for earlier weeks.
- High and low come from consolidated (SIP) minute bars. Against Nasdaq-only bars, spot and the
  day's extremes agree to a median of 0 bp (95% within ±3.5 bp).
