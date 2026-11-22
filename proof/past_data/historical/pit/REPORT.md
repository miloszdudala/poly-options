# Test 3: PIT on historical options, 10 samples

## Description

- **The question.** Calibration asks about one barrier per point. The PIT (probability integral
  transform) uses the whole range of barriers:
  - Take the highest price the stock actually reached before the deadline, and ask the model how
    likely it was to touch at least that level. Do the same with the lowest price.
  - If the model is right, these probabilities are uniformly spread. Converted to normal scores they
    have mean mu = 0 and spread s = 1.
  - mu below 0 means prices moved less than the model expected; above 0, more. s below 1 means the
    model's spread of outcomes is too wide.
- **The method** is Berkowitz (2001), a standard test for risk models, used unchanged.
- **No tails cut.** The region is every barrier the model can price, i.e. every listed strike with a
  usable volatility. A move beyond the last such strike tells us only that it went past it (a
  censored observation). The test cannot see errors beyond the listed strikes.
- **Data.** The same data and exclusions as [test 2](../calibration/REPORT.md). Ten new samples, one point per week each
  (drawn the same way), none shared with test 2 or with each other. Every point is used for both directions.
- **Intervals** come from resampling whole weeks.
- **Synthetic check.** The test was first run on simulated data where the answer is known. With the
  true model, mu came out at -0.002 and s at 1.000 on average. Volatility 10% off was flagged in
  every run (mu about ±0.11).

The observations of every sample (extreme reached, region, normal score) are in
this folder (`sample_01.csv` to `sample_10.csv`).

## Performance

| Sample | Observations | Inside region / below / beyond | mu | 95% interval | s | 95% interval |
|---:|---:|---|---:|---|---:|---|
| 1 | 856 | 706 / 144 / 6 | -0.004 | -0.040 to +0.033 | 0.972 | 0.914 to 1.033 |
| 2 | 858 | 692 / 159 / 7 | -0.027 | -0.065 to +0.010 | 0.962 | 0.908 to 1.018 |
| 3 | 850 | 685 / 153 / 12 | -0.039 | -0.076 to -0.002 | 0.982 | 0.919 to 1.043 |
| 4 | 842 | 685 / 151 / 6 | -0.002 | -0.040 to +0.034 | 0.961 | 0.907 to 1.020 |
| 5 | 854 | 684 / 162 / 8 | -0.002 | -0.046 to +0.041 | 1.012 | 0.946 to 1.080 |
| 6 | 856 | 679 / 167 / 10 | -0.019 | -0.057 to +0.020 | 0.986 | 0.932 to 1.043 |
| 7 | 848 | 696 / 142 / 10 | -0.022 | -0.060 to +0.015 | 0.962 | 0.903 to 1.022 |
| 8 | 844 | 688 / 148 / 8 | +0.004 | -0.033 to +0.042 | 1.007 | 0.945 to 1.071 |
| 9 | 832 | 676 / 147 / 9 | -0.024 | -0.062 to +0.012 | 0.926 | 0.873 to 0.980 |
| 10 | 838 | 681 / 149 / 8 | +0.004 | -0.034 to +0.042 | 1.000 | 0.941 to 1.060 |
| **All 10** | **8,478** | | **-0.013** | **-0.031 to +0.005** | **0.977** | **0.951 to 1.002** |

In plain words *(unapproved)*: both mu = 0 and s = 1 are inside their intervals. Converted to touch
probabilities, the best estimate puts the model 0.5 to 0.9 points too high depending on the barrier
(30% where 29% would be right), and the intervals include no error at all. [Test 2](../calibration/REPORT.md) found +0.1 points.
Either way the error is well inside the 3c margin every bet must clear.

The 10 samples share the same weeks and are correlated (ICC +0.163, worth about 4 independent samples); see [The samples are correlated](../../REPORT.md#the-samples-are-correlated).

Back to the [summary](../../REPORT.md).
