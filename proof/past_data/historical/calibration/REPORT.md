# Test 2: calibration on historical options, 10 samples

## Description

- **Data.**
  - Options: Databento OPRA, one minute of the whole option chain (best bid and ask) at a random
    quarter-hour moment, many moments per week, May 2018 to November 2026, 19 underlyings (the
    stocks and ETFs Polymarket lists; the S&P 500 index is left out for lack of minute data).
  - Prices: Alpaca SIP minute bars from that moment to the week's last close.
- **A point** is one (moment, stock). Each of the 10 samples has **one point per week**: points of
  that week are drawn at random from everything downloaded, and the first one that passes the
  exclusions below is used. No point is used twice, in this test or the next.
  For each point a direction (up or down, 50/50) and a listed strike 0.5% to 2.5 standard deviations
  away are drawn at random. That strike is the barrier, as in a weekly hit market.
- **The model** prices the barrier exactly as it does live: the session-hours touch probability, the
  implied volatility at the barrier strike of the first expiry at or after the deadline, and the
  session/overnight variance split from the stock's past daily bars. Then we check whether the price
  touched the barrier during regular hours before the deadline.
- **Points excluded** before pricing (3,720 drawn points in both tests, listed in
  [`excluded.csv`](../excluded.csv)):
  - an earnings report between the moment and the deadline, or between the deadline and the option
    expiry the volatility comes from. The model does not trade these weeks live, so they are not
    tested. Report dates are known in advance, so this does not depend on the outcome;
  - an option chain too thin to build a smile (mostly Friday afternoons, hours before expiry);
  - no trade in the minute before the moment;
  - no strike in range, or no volatility the model accepts at the barrier;
  - a stock split inside the week;
  - a spot price that disagrees with the option chain by more than 2%.
- **Measures.**
  - d = actual touches minus predicted, per point. A perfect model has d = 0.
  - 95% intervals come from resampling whole weeks.
  - Brier score, against a constant base rate.
  - Cox slope: 1 means the model is neither over- nor under-confident.
- The test was first run on simulated data where the answer is known (same stocks and dates).
  With the true model, the interval covered d = 0 in 95% of runs. With volatility 10% too high or too low, d moved to about
  ±3.6 points.

The points of every sample, with the model's price and the outcome, are in
this folder (`sample_01.csv` to `sample_10.csv`).

## Performance

| Sample | Points | Touched | Predicted | d (pp) | 95% interval (pp) | Brier | Base rate Brier | Cox slope |
|---:|---:|---:|---:|---:|---|---:|---:|---:|
| 1 | 426 | 116 | 111.0 | +1.2 | -2.4 to +4.5 | 0.138 | 0.198 | 1.09 |
| 2 | 420 | 115 | 105.5 | +2.3 | -1.3 to +5.9 | 0.138 | 0.199 | 1.11 |
| 3 | 425 | 98 | 111.2 | -3.1 | -6.6 to +0.5 | 0.135 | 0.177 | 0.92 |
| 4 | 419 | 111 | 108.5 | +0.6 | -3.0 to +4.2 | 0.140 | 0.195 | 1.05 |
| 5 | 421 | 117 | 106.6 | +2.5 | -1.1 to +6.1 | 0.149 | 0.201 | 1.03 |
| 6 | 419 | 98 | 105.9 | -1.9 | -5.3 to +1.5 | 0.131 | 0.179 | 1.05 |
| 7 | 425 | 110 | 110.8 | -0.2 | -3.8 to +3.3 | 0.138 | 0.192 | 1.06 |
| 8 | 415 | 94 | 101.2 | -1.7 | -5.2 to +2.0 | 0.141 | 0.175 | 0.82 |
| 9 | 428 | 113 | 108.2 | +1.1 | -2.6 to +5.0 | 0.158 | 0.194 | 0.82 |
| 10 | 419 | 105 | 103.0 | +0.5 | -2.9 to +4.0 | 0.134 | 0.188 | 1.08 |
| **All 10** | **4,217** | **1,077** | **1,071.7** | **+0.1** | **-1.1 to +1.3** | **0.140** | **0.190** | **0.99** |

In plain words *(unapproved)*: over 4,217 barriers the model predicted 1,072 touches and 1,077
happened. All ten intervals contain zero.

The 10 samples share the same weeks and are practically independent (ICC +0.012, worth about 9 independent samples); see [The samples are correlated](../../REPORT.md#the-samples-are-correlated).

Back to the [summary](../../REPORT.md).
