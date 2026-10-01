# Calibration of Polymarket hit markets, 2026-02 to 2026-08

Generated 2026-09-18 by `model/run_calibration.py` from the backfill in `data/src/poly_backfill.py`. No option data is used anywhere in this report.

## 1. Sample

- **7,113 resolved hit markets** (1,596,109 hourly price points), 19 tickers, deadline months 2026-02, 2026-03, 2026-04, 2026-05, 2026-06, 2026-07, 2026-08.
- 1,031 markets dropped for a September deadline: a touched market closes early while an untouched one waits for period end, so a month still in progress over-represents YES.
- Weekly: 5,473 markets; monthly: 1,640.
- Base rate, **per market**: 0.367 resolved YES (2,607 of 7,113).
- Base rate, **per price point**: 0.225. It is lower because a market that touches stops quoting at that moment while an untouched one quotes until period end, so NO outcomes contribute more hours. Every table below is per point, which is the right weighting for "if I bought whenever the price was p" and the wrong one for "of markets priced p".
- Empirical touch tables built for 18 tickers from sessions strictly before 2026-02-01, so every scored point is out of sample.

- Excluded from the headline tables: 160,508 points quoted at exactly 0.5000 (section 6.1) and, from sections 4 and 5, 26,645 touch-pending points (section 6.2).

> **A price point is a mid proxy, not a tradeable quote.** Polymarket's `prices-history` returns one price per hour with no size and no side. Sections 2 and 3 measure where the market was *marked*; only section 5, which adds half the 7c median spread and the 4% taker fee, is about money.

## 2. Reliability, YES side

Every hourly point while the market was open, bucketed by its YES price. `gap` is realised minus mean price: **negative means the bucket was dear**. The interval to read is the clustered one: the points are not independent, because every hour of one market shares that market's single outcome.

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 330,663 | 3,953 | 0.014 | 0.002 | -0.011 | -0.012 to -0.010 | -0.012 to -0.011 |
| 3-5c | 125,078 | 3,891 | 0.038 | 0.023 | -0.015 | -0.023 to -0.007 | -0.016 to -0.014 |
| 5-10c | 188,170 | 4,349 | 0.072 | 0.043 | -0.029 | -0.039 to -0.018 | -0.029 to -0.028 |
| 10-20c | 188,222 | 4,432 | 0.138 | 0.102 | -0.036 | -0.052 to -0.020 | -0.037 to -0.035 |
| 20-35c | 155,003 | 4,289 | 0.269 | 0.234 | -0.035 | -0.058 to -0.012 | -0.037 to -0.033 |
| 35-50c | 193,439 | 5,677 | 0.445 | 0.357 | -0.088 | -0.112 to -0.064 | -0.090 to -0.086 |
| 50-65c | 146,951 | 4,934 | 0.557 | 0.529 | -0.028 | -0.053 to -0.004 | -0.031 to -0.026 |
| >65c | 108,075 | 3,345 | 0.776 | 0.775 | -0.001 | -0.025 to +0.023 | -0.004 to +0.001 |

**Read: the market overprices YES at every price level.** A 1.4c contract resolves YES 0.2% of the time; a 44.6c contract resolves YES 36%. Only the top bucket is fairly priced. This is FINDINGS 3.2's favourite-longshot bias measured against outcomes instead of against an option-implied fair, and it is larger than the option comparison suggested.

### 2.1 The same table with placeholder mids left in

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 330,663 | 3,953 | 0.014 | 0.002 | -0.011 | -0.012 to -0.010 | -0.012 to -0.011 |
| 3-5c | 125,078 | 3,891 | 0.038 | 0.023 | -0.015 | -0.023 to -0.007 | -0.016 to -0.014 |
| 5-10c | 188,170 | 4,349 | 0.072 | 0.043 | -0.029 | -0.039 to -0.018 | -0.029 to -0.028 |
| 10-20c | 188,222 | 4,432 | 0.138 | 0.102 | -0.036 | -0.052 to -0.020 | -0.037 to -0.035 |
| 20-35c | 155,003 | 4,289 | 0.269 | 0.234 | -0.035 | -0.058 to -0.012 | -0.037 to -0.033 |
| 35-50c | 193,439 | 5,677 | 0.445 | 0.357 | -0.088 | -0.112 to -0.064 | -0.090 to -0.086 |
| 50-65c | 307,459 | 6,779 | 0.527 | 0.450 | -0.077 | -0.097 to -0.058 | -0.079 to -0.075 |
| >65c | 108,075 | 3,345 | 0.776 | 0.775 | -0.001 | -0.025 to +0.023 | -0.004 to +0.001 |

### 2.2 By period

**weekly** (587,568 points, 5,451 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 88,560 | 2,982 | 0.016 | 0.004 | -0.012 | -0.014 to -0.010 | -0.013 to -0.012 |
| 3-5c | 50,139 | 2,902 | 0.037 | 0.022 | -0.015 | -0.023 to -0.006 | -0.016 to -0.013 |
| 5-10c | 80,302 | 3,315 | 0.071 | 0.043 | -0.028 | -0.037 to -0.020 | -0.030 to -0.027 |
| 10-20c | 84,998 | 3,434 | 0.138 | 0.093 | -0.045 | -0.057 to -0.032 | -0.047 to -0.043 |
| 20-35c | 76,070 | 3,273 | 0.270 | 0.199 | -0.071 | -0.089 to -0.053 | -0.074 to -0.068 |
| 35-50c | 80,650 | 4,148 | 0.438 | 0.312 | -0.125 | -0.145 to -0.105 | -0.129 to -0.122 |
| 50-65c | 71,597 | 3,652 | 0.562 | 0.549 | -0.013 | -0.036 to +0.010 | -0.017 to -0.009 |
| >65c | 55,252 | 2,516 | 0.763 | 0.798 | +0.035 | +0.014 to +0.056 | +0.032 to +0.038 |

**monthly** (848,033 points, 1,628 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 242,103 | 971 | 0.013 | 0.002 | -0.011 | -0.012 to -0.010 | -0.011 to -0.011 |
| 3-5c | 74,939 | 989 | 0.039 | 0.023 | -0.016 | -0.028 to -0.003 | -0.017 to -0.014 |
| 5-10c | 107,868 | 1,034 | 0.072 | 0.043 | -0.029 | -0.046 to -0.011 | -0.030 to -0.027 |
| 10-20c | 103,224 | 998 | 0.139 | 0.110 | -0.029 | -0.055 to -0.002 | -0.031 to -0.027 |
| 20-35c | 78,933 | 1,016 | 0.268 | 0.268 | -0.000 | -0.042 to +0.041 | -0.003 to +0.003 |
| 35-50c | 112,789 | 1,529 | 0.450 | 0.389 | -0.061 | -0.099 to -0.024 | -0.064 to -0.059 |
| 50-65c | 75,354 | 1,282 | 0.551 | 0.509 | -0.042 | -0.085 to +0.000 | -0.046 to -0.039 |
| >65c | 52,823 | 829 | 0.790 | 0.751 | -0.039 | -0.082 to +0.004 | -0.043 to -0.035 |

### 2.3 By direction

**hit_up** (696,296 points, 3,537 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 154,913 | 1,825 | 0.013 | 0.004 | -0.009 | -0.011 to -0.007 | -0.009 to -0.009 |
| 3-5c | 56,237 | 1,851 | 0.038 | 0.037 | -0.001 | -0.014 to +0.012 | -0.002 to +0.001 |
| 5-10c | 90,099 | 2,092 | 0.072 | 0.062 | -0.010 | -0.029 to +0.009 | -0.011 to -0.008 |
| 10-20c | 90,488 | 2,153 | 0.139 | 0.119 | -0.020 | -0.043 to +0.003 | -0.022 to -0.018 |
| 20-35c | 79,407 | 2,136 | 0.269 | 0.257 | -0.012 | -0.045 to +0.021 | -0.015 to -0.009 |
| 35-50c | 99,204 | 2,839 | 0.444 | 0.389 | -0.055 | -0.089 to -0.021 | -0.058 to -0.052 |
| 50-65c | 72,097 | 2,501 | 0.558 | 0.555 | -0.003 | -0.037 to +0.030 | -0.007 to +0.000 |
| >65c | 53,851 | 1,810 | 0.778 | 0.797 | +0.019 | -0.012 to +0.050 | +0.016 to +0.022 |

**hit_down** (739,305 points, 3,542 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 175,750 | 2,128 | 0.014 | 0.001 | -0.013 | -0.014 to -0.013 | -0.013 to -0.013 |
| 3-5c | 68,841 | 2,040 | 0.038 | 0.011 | -0.027 | -0.038 to -0.016 | -0.028 to -0.026 |
| 5-10c | 98,071 | 2,257 | 0.071 | 0.025 | -0.046 | -0.056 to -0.035 | -0.047 to -0.045 |
| 10-20c | 97,734 | 2,279 | 0.138 | 0.087 | -0.050 | -0.072 to -0.029 | -0.052 to -0.049 |
| 20-35c | 75,596 | 2,153 | 0.269 | 0.209 | -0.059 | -0.092 to -0.027 | -0.062 to -0.056 |
| 35-50c | 94,235 | 2,838 | 0.446 | 0.322 | -0.123 | -0.156 to -0.091 | -0.126 to -0.120 |
| 50-65c | 74,854 | 2,433 | 0.555 | 0.503 | -0.052 | -0.088 to -0.016 | -0.056 to -0.049 |
| >65c | 54,224 | 1,535 | 0.774 | 0.753 | -0.021 | -0.057 to +0.015 | -0.025 to -0.017 |

### 2.4 By time to deadline

**<4h** (17,326 points, 4,582 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 11,667 | 3,510 | 0.010 | 0.000 | -0.010 | -0.010 to -0.010 | -0.010 to -0.010 |
| 3-5c | 1,673 | 952 | 0.038 | 0.002 | -0.036 | -0.038 to -0.034 | -0.038 to -0.033 |
| 5-10c | 1,991 | 956 | 0.066 | 0.004 | -0.062 | -0.066 to -0.059 | -0.064 to -0.059 |
| 10-20c | 726 | 418 | 0.115 | 0.012 | -0.102 | -0.112 to -0.093 | -0.108 to -0.091 |
| 20-35c | 204 | 139 | 0.273 | 0.074 | -0.200 | -0.237 to -0.162 | -0.228 to -0.156 |
| 35-50c | 787 | 356 | 0.485 | 0.030 | -0.454 | -0.474 to -0.434 | -0.464 to -0.440 |
| 50-65c | 77 | 51 | 0.536 | 0.247 | -0.289 | -0.419 to -0.160 | -0.372 to -0.182 |
| >65c | 201 | 91 | 0.984 | 0.980 | -0.004 | -0.024 to +0.016 | -0.034 to +0.008 |

**4-24h** (86,390 points, 4,676 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 45,016 | 3,528 | 0.011 | 0.001 | -0.010 | -0.011 to -0.009 | -0.010 to -0.009 |
| 3-5c | 9,662 | 1,505 | 0.037 | 0.008 | -0.029 | -0.033 to -0.024 | -0.030 to -0.026 |
| 5-10c | 11,475 | 1,819 | 0.072 | 0.032 | -0.040 | -0.050 to -0.029 | -0.043 to -0.036 |
| 10-20c | 9,187 | 1,486 | 0.125 | 0.059 | -0.066 | -0.081 to -0.050 | -0.070 to -0.061 |
| 20-35c | 3,769 | 793 | 0.265 | 0.206 | -0.059 | -0.099 to -0.018 | -0.071 to -0.046 |
| 35-50c | 4,611 | 903 | 0.459 | 0.152 | -0.307 | -0.338 to -0.276 | -0.317 to -0.297 |
| 50-65c | 1,594 | 349 | 0.553 | 0.496 | -0.056 | -0.130 to +0.018 | -0.081 to -0.032 |
| >65c | 1,076 | 224 | 0.814 | 0.784 | -0.030 | -0.109 to +0.049 | -0.056 to -0.006 |

**1-3d** (216,355 points, 5,238 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 67,701 | 2,788 | 0.013 | 0.003 | -0.010 | -0.012 to -0.008 | -0.010 to -0.010 |
| 3-5c | 25,825 | 2,342 | 0.038 | 0.020 | -0.017 | -0.024 to -0.010 | -0.019 to -0.015 |
| 5-10c | 35,956 | 2,789 | 0.070 | 0.040 | -0.030 | -0.039 to -0.022 | -0.032 to -0.028 |
| 10-20c | 29,758 | 2,585 | 0.136 | 0.099 | -0.037 | -0.052 to -0.022 | -0.041 to -0.034 |
| 20-35c | 20,568 | 1,992 | 0.266 | 0.209 | -0.057 | -0.081 to -0.033 | -0.063 to -0.051 |
| 35-50c | 18,084 | 2,107 | 0.437 | 0.276 | -0.161 | -0.187 to -0.135 | -0.167 to -0.154 |
| 50-65c | 11,517 | 1,422 | 0.558 | 0.478 | -0.079 | -0.117 to -0.041 | -0.088 to -0.070 |
| >65c | 6,946 | 887 | 0.787 | 0.754 | -0.033 | -0.074 to +0.008 | -0.043 to -0.023 |

**3-7d** (426,773 points, 6,384 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 67,381 | 1,477 | 0.013 | 0.003 | -0.010 | -0.012 to -0.007 | -0.010 to -0.009 |
| 3-5c | 26,548 | 1,712 | 0.037 | 0.030 | -0.007 | -0.021 to +0.006 | -0.009 to -0.005 |
| 5-10c | 46,539 | 2,481 | 0.072 | 0.053 | -0.019 | -0.031 to -0.006 | -0.021 to -0.017 |
| 10-20c | 55,509 | 2,982 | 0.141 | 0.096 | -0.045 | -0.059 to -0.030 | -0.047 to -0.042 |
| 20-35c | 57,120 | 3,092 | 0.271 | 0.197 | -0.075 | -0.094 to -0.056 | -0.078 to -0.071 |
| 35-50c | 63,577 | 3,984 | 0.437 | 0.324 | -0.114 | -0.135 to -0.092 | -0.117 to -0.110 |
| 50-65c | 61,225 | 3,447 | 0.563 | 0.558 | -0.005 | -0.029 to +0.018 | -0.009 to -0.001 |
| >65c | 48,874 | 2,124 | 0.758 | 0.801 | +0.043 | +0.021 to +0.065 | +0.040 to +0.047 |

**>7d** (688,757 points, 1,429 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 138,898 | 645 | 0.015 | 0.002 | -0.013 | -0.015 to -0.012 | -0.014 to -0.013 |
| 3-5c | 61,370 | 750 | 0.039 | 0.024 | -0.015 | -0.030 to -0.001 | -0.016 to -0.014 |
| 5-10c | 92,209 | 860 | 0.072 | 0.041 | -0.031 | -0.049 to -0.012 | -0.032 to -0.029 |
| 10-20c | 93,042 | 919 | 0.139 | 0.112 | -0.027 | -0.055 to +0.001 | -0.029 to -0.025 |
| 20-35c | 73,342 | 932 | 0.268 | 0.271 | +0.003 | -0.040 to +0.046 | +0.000 to +0.007 |
| 35-50c | 106,380 | 1,381 | 0.450 | 0.402 | -0.048 | -0.087 to -0.010 | -0.051 to -0.045 |
| 50-65c | 72,538 | 1,197 | 0.551 | 0.513 | -0.038 | -0.081 to +0.004 | -0.042 to -0.035 |
| >65c | 50,978 | 760 | 0.791 | 0.752 | -0.039 | -0.083 to +0.005 | -0.042 to -0.035 |

## 3. Reliability, NO side

The same points priced as `1 - YES`, winning when the market resolved NO. It is the mirror of section 2 and is tabulated separately because the NO side is where the capacity is (FINDINGS section 7: ~$616k on NO against ~$89k on YES at one snapshot).

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 5,987 | 1,572 | 0.008 | 0.001 | -0.007 | -0.008 to -0.007 | -0.008 to -0.006 |
| 3-5c | 2,381 | 354 | 0.040 | 0.040 | +0.001 | -0.030 to +0.032 | -0.007 to +0.009 |
| 5-10c | 7,783 | 982 | 0.074 | 0.097 | +0.023 | -0.009 to +0.055 | +0.017 to +0.030 |
| 10-20c | 21,684 | 1,746 | 0.157 | 0.192 | +0.036 | -0.000 to +0.072 | +0.031 to +0.041 |
| 20-35c | 67,201 | 2,851 | 0.283 | 0.273 | -0.010 | -0.037 to +0.017 | -0.013 to -0.007 |
| 35-50c | 149,990 | 4,944 | 0.441 | 0.468 | +0.027 | +0.002 to +0.051 | +0.024 to +0.029 |
| 50-65c | 189,162 | 5,663 | 0.553 | 0.642 | +0.089 | +0.065 to +0.113 | +0.087 to +0.091 |
| >65c | 991,413 | 5,440 | 0.907 | 0.931 | +0.024 | +0.015 to +0.032 | +0.023 to +0.024 |

### 3.1 By period

**weekly** (587,568 points, 5,451 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 2,635 | 1,171 | 0.004 | 0.001 | -0.003 | -0.004 to -0.002 | -0.004 to -0.001 |
| 3-5c | 695 | 202 | 0.039 | 0.032 | -0.007 | -0.035 to +0.020 | -0.018 to +0.009 |
| 5-10c | 3,207 | 659 | 0.074 | 0.081 | +0.007 | -0.021 to +0.035 | -0.002 to +0.017 |
| 10-20c | 9,741 | 1,239 | 0.158 | 0.162 | +0.004 | -0.028 to +0.037 | -0.003 to +0.012 |
| 20-35c | 37,167 | 2,128 | 0.287 | 0.237 | -0.049 | -0.075 to -0.024 | -0.054 to -0.045 |
| 35-50c | 73,404 | 3,662 | 0.436 | 0.446 | +0.011 | -0.012 to +0.034 | +0.007 to +0.014 |
| 50-65c | 78,462 | 4,134 | 0.560 | 0.686 | +0.126 | +0.106 to +0.146 | +0.123 to +0.129 |
| >65c | 382,257 | 4,155 | 0.890 | 0.926 | +0.036 | +0.028 to +0.043 | +0.035 to +0.036 |

**monthly** (848,033 points, 1,628 markets)

| bucket | points | markets | mean price | realised | gap | 95% on the gap, clustered by market | 95%, binomial |
|---|---:|---:|---:|---:|---:|---|---|
| <3c | 3,352 | 401 | 0.011 | 0.000 | -0.011 | -0.011 to -0.010 | -0.011 to -0.009 |
| 3-5c | 1,686 | 152 | 0.040 | 0.044 | +0.004 | -0.039 to +0.046 | -0.005 to +0.015 |
| 5-10c | 4,576 | 323 | 0.074 | 0.108 | +0.034 | -0.016 to +0.084 | +0.026 to +0.044 |
| 10-20c | 11,943 | 507 | 0.155 | 0.217 | +0.062 | +0.002 to +0.122 | +0.054 to +0.069 |
| 20-35c | 30,034 | 723 | 0.278 | 0.317 | +0.039 | -0.013 to +0.091 | +0.034 to +0.044 |
| 35-50c | 76,586 | 1,282 | 0.447 | 0.489 | +0.042 | -0.001 to +0.084 | +0.038 to +0.045 |
| 50-65c | 110,700 | 1,529 | 0.548 | 0.611 | +0.063 | +0.025 to +0.101 | +0.060 to +0.066 |
| >65c | 609,156 | 1,285 | 0.918 | 0.934 | +0.016 | +0.004 to +0.029 | +0.016 to +0.017 |

## 4. Three forecasts against the same outcomes

Restricted to the 1,569,464 points where spot was still on the safe side of the barrier. The other 26,645 are touch-pending (MODELS 3.1) and are scored in section 6 instead.

| forecast | n | mean forecast | Brier | vs base rate |
|---|---:|---:|---:|---:|
| market price | 1,414,890 | 0.231 | 0.1091 | -0.0488 |
| empirical fair | 1,396,228 | 0.195 | 0.1030 | -0.0549 |
| continuous one-touch | 1,414,890 | 0.207 | 0.1047 | -0.0532 |
| base rate (0.197) | 1,414,890 | 0.197 | 0.1579 | 0.0000 |

Displacement by bucket - mid against each fair value, never `bid - fair` or `fair - ask` (FINDINGS section 2):

| bucket | n | median mid | median empirical | median continuous | realised YES rate |
|---|---:|---:|---:|---:|---:|
| <3c | 330,663 | 0.013 | 0.006 | 0.000 | 0.002 |
| 3-5c | 125,078 | 0.038 | 0.023 | 0.008 | 0.023 |
| 5-10c | 188,170 | 0.070 | 0.047 | 0.030 | 0.043 |
| 10-20c | 188,221 | 0.135 | 0.108 | 0.102 | 0.102 |
| 20-35c | 154,998 | 0.265 | 0.222 | 0.240 | 0.234 |
| 35-50c | 191,051 | 0.455 | 0.316 | 0.346 | 0.349 |
| 50-65c | 142,616 | 0.550 | 0.522 | 0.572 | 0.517 |
| >65c | 94,093 | 0.740 | 0.743 | 0.799 | 0.750 |

## 5. Paper PnL

Costs: entry at the hourly mid plus 3.5c (half the 7c median top-of-book spread) plus the taker fee `0.04 x min(p, 1-p)` on what was paid, held to resolution. No depth check: FINDINGS section 7 measured a median $52-$719 available per market, so these unit counts are not all fillable.

**buy NO when YES <= 10c** - one unit per market, entered at the first hourly point that fires, paying mid + 3.5c + 4% fee, held to resolution.

| deadline month | bets | staked | PnL | ROI | share resolved in your favour |
|---|---:|---:|---:|---:|---:|
| 2026-02 | 100 | $96 | $+3 | +2.9% | 0.870 |
| 2026-03 | 53 | $51 | $-0 | -0.4% | 0.811 |
| 2026-04 | 722 | $698 | $-43 | -6.1% | 0.766 |
| 2026-05 | 969 | $926 | $-6 | -0.7% | 0.906 |
| 2026-06 | 764 | $731 | $-27 | -3.6% | 0.873 |
| 2026-07 | 980 | $944 | $-55 | -5.8% | 0.791 |
| 2026-08 | 1,120 | $1,084 | $-18 | -1.6% | 0.817 |
| **total** | **4,708** | **$4,529** | **$-145** | **-3.2%** | **0.832** |

**buy YES when empirical - price >= 5c** - one unit per market, entered at the first hourly point that fires, paying mid + 3.5c + 4% fee, held to resolution.

| deadline month | bets | staked | PnL | ROI | share resolved in your favour |
|---|---:|---:|---:|---:|---:|
| 2026-02 | 69 | $30 | $+1 | +3.0% | 0.449 |
| 2026-03 | 30 | $13 | $+0 | +3.1% | 0.433 |
| 2026-04 | 639 | $273 | $+85 | +31.2% | 0.560 |
| 2026-05 | 911 | $384 | $+6 | +1.5% | 0.428 |
| 2026-06 | 807 | $349 | $+48 | +13.8% | 0.492 |
| 2026-07 | 1,064 | $425 | $+68 | +15.9% | 0.463 |
| 2026-08 | 1,168 | $427 | $-11 | -2.5% | 0.356 |
| **total** | **4,688** | **$1,901** | **$+197** | **+10.3%** | **0.448** |


### 5.1 How independent are these bets?

| rule | bets | markets | tickers | ticker-weeks | largest ticker's share of PnL |
|---|---:|---:|---:|---:|---:|
| buy NO when YES <= 10c | 4,708 | 4,708 | 19 | 404 | 15% |
| buy YES when empirical - price >= 5c | 4,688 | 4,688 | 18 | 400 | 22% |

### 5.2 Control

MODELS 4.5: the same side, the same number of bets, the same price buckets, markets drawn at random. If the control makes the same money, the signal did nothing and the market simply drifted.

| rule | rule ROI | control ROI (mean of draws) | control sd | control range | draws |
|---|---:|---:|---:|---|---:|
| buy NO when YES <= 10c | -3.2% | -1.5% | 0.1% | -1.7% to -1.3% | 20 |
| buy YES when empirical - price >= 5c | +10.3% | -24.5% | 1.4% | -27.7% to -22.8% | 20 |

## 6. Data-quality exclusions

### 6.1 Placeholder mids

160,508 points (10.1% of the sample, across 6,459 markets) are quoted at **exactly 0.5000**. They are 52% of the 50-65c bucket and they are not quotes:

| | at exactly 0.5000 | rest of the 50-65c bucket |
|---|---:|---:|
| points | 160,508 | 146,951 |
| median barrier distance k (in realised moves) | 0.91 | 0.56 |
| median empirical fair | 0.311 | 0.522 |
| realised YES rate | 0.378 | 0.529 |

and by time left, a contract still marked at 50c:

| hours to deadline | points | realised YES rate |
|---|---:|---:|
| <4h | 351 | 0.040 |
| 4-24h | 2,877 | 0.105 |
| 1-3d | 9,207 | 0.152 |
| 3-7d | 67,952 | 0.381 |
| >7d | 80,121 | 0.413 |

A contract four hours from its deadline, with the barrier a full realised move away, that resolves YES 4.0% of the time, is not a market saying 50/50. It is the venue marking an hour in which there was nothing to mark - the same class of artifact as FINDINGS section 4's untraded Cboe contracts, whose IV came from a placeholder quote. These points are excluded from the headline tables and from both trading rules, and section 2.1 repeats the headline with them left in so the difference is visible rather than asserted.

### 6.2 Touch-pending points

26,645 points (1.7% of the sample, 959 markets) had the hourly bar close already beyond the barrier while the market was still quoting. 0.942 of them resolved YES, at a median quote of 0.700.

That gap is not a tradeable edge until it is shown not to be a data artifact. The spot here is a yfinance hourly bar close and the venue resolves on Pyth 1-minute candles, so a bar that closes past the barrier is consistent with (a) a resolution the archive stamps a few minutes later, (b) the two feeds disagreeing on the print, or (c) a genuinely slow book. Only (c) is money. Telling them apart needs the trade tape (FINDINGS section 7) and minute bars, neither of which this report has, so these points are excluded from sections 4 and 5 as MODELS 3.1 requires.

## 7. What this answers, and what it does not

**Answered.** FINDINGS 5.3 asked whether contracts priced at 3-5c resolve YES 3-5% of the time. They resolve YES 2.3% of the time, and every bucket below 65c is dear by 1-9c against outcomes. The favourite-longshot bias in FINDINGS 3.2 is real and it is bigger than the option-implied comparison showed. Nothing here depends on an option chain, an implied vol or a skew choice.

**Answered, unexpectedly.** The historical touch frequency, built only from daily bars before 2026-02-01, forecasts outcomes better than the market price does (section 4), and a rule that trades on it separates cleanly from a price-matched random control (section 5.2). MODELS 5.3's `empirical_longshot` is the model worth building first.

**Contradicted.** MODELS 5.3's first shape - sell longshots, buy NO when the YES ask is under ~12c - loses money here. Section 2 says a 7.2c contract resolves YES 4.3% of the time, so the NO side is 2.9c cheap at mid; section 5 says that buying it at mid + 3.5c + fee returns -3.2%, which is worse than buying NO at random at the same prices. The displacement is real and it is smaller than the spread. FINDINGS 3.3 said this in advance - **do not buy cheap contracts** - and the NO side of a longshot is a cheap contract wearing a 90c price tag. Section 3 puts a number on it where the capacity actually is: the NO side above 65c, 991,413 points at a mean 0.907, is +0.024 cheap at mid - against a 3.5c half-spread it does not clear its own costs.

**Not answered.** Whether any of this is fillable. Every entry above is a mid plus half a median spread, with no book, no depth and no queue. FINDINGS section 7 measured $52-$719 of depth per market and a fill price that ran 0.114 -> 0.591 for $0 -> $500 on one ladder, so the +10.3% is an upper bound on a number that the order book gets to veto. The next thing to measure is that veto, against the live books now being collected.
