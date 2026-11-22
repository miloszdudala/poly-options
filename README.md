# poly-options

Some Polymarket bets on stock prices are, in substance, options. Options on US exchanges are traded in large volume, mostly by professionals, so this project assumes their prices are close to the truth, derives from them the fair probability of each Polymarket bet, and bets where Polymarket is mispriced.

So far:
- The pricing engine was checked on 4,217 historical barriers (2018–2026): it predicted 1,072 touches, and 1,077 happened.
- Replayed over 9 weeks of Polymarket data (16 September – 13 November 2026), the model made $29.58 on $297.42 staked (9.9%), while random bets in the same weeks lost $54.83 on average.
- Since 18 November 2026 the model reports its bets live, before they settle. As of 22 November: one week, $8.09 on $25.86 staked (31.3%).

All bets are on paper; no real money is placed.

## Results

### Pricing engine against what prices actually did (May 2018 – November 2026, 19 stocks and ETFs)

| Test | Sample | Result | Ideal |
|---|---:|---|---|
| Calibration | 4,217 barriers | 1,072 touches predicted, 1,077 happened; difference +0.1 percentage points (95% interval −1.1 to +1.3) | 0 |
| PIT | 8,478 observations | μ = −0.013 (−0.031 to +0.005), s = 0.977 (0.951 to 1.002) | μ = 0, s = 1 |

### Full model on Polymarket weekly hit markets (one share of every bet, held to resolution)

| Record | Period | Bets | Won / expected | Staked | EV promised | Profit | Return | Losing weeks |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Replay | 16 Sep – 13 Nov 2026 | 625 | 327 / 334.7 | $297.42 | $37.33 | $29.58 | 9.9% | 1 of 9 |
| Random bets, same weeks (2,000 runs) | 16 Sep – 13 Nov 2026 | 625 | | | | −$54.83 on average (95% range −$71.43 to −$37.91) | −22% | 8.9 of 9 on average |
| Live | from 18 Nov 2026 | 47 | 34 / 28.2 | $25.86 | $2.30 | $8.09 | 31.3% | 0 of 1 |

How each number was obtained, and what it does and does not show, is described in [Testing](#testing) and [Caveats](#caveats). Full reports: [`proof/past_data/REPORT.md`](proof/past_data/REPORT.md) and [`proof/live_data/`](proof/live_data/).

## The idea

Some Polymarket bets carry a resemblance to options traded on US stock exchanges. E.g. "Will NVIDIA (NVDA) hit (HIGH) $164 Week of November 16 2026?" or "Will Amazon (AMZN) close above $230 end of September?"

I've learnt from a YouTube video that the Black-Scholes model is a standard way to price options nowadays, and that a little bit of math allows us to derive, from publicly available options data, the probability that a stock touches or closes above a given price before a given date, which is exactly what these bets are on.

The project assumes that if we had a perfect 1-to-1 comparison of a Polymarket bet to a Nasdaq/NYSE option, the option would be much closer to the truth, since its volume is significantly higher and mainly dictated by professionals in the industry, whereas Polymarket, I suppose, is mostly internet gamblers. This assumption should, but does not have to, be true: Polymarket markets are small, and perhaps this allows them, for example, to reflect news faster. Whether option pricing is consistently wrong is outside the scope of this project; we assume it to be correct.

The comparison is, however, not perfect and needs some amount of interpolation. The main obstacles are:
- information from the stock exchange needs to be translated into a probability;
- there may be no option at the exact price a bet is on;
- the expiry date of the option may differ from the bet's deadline.

The amount of approximation goes beyond what I originally assumed, and I believe that at the current stage some of it is done suboptimally.

## The model

The model has two stages: a pricing engine that turns option data into a probability, and a trading strategy that uses those probabilities to decide which bets to take. At the moment only weekly hit markets on stocks and ETFs are traded.

### Pricing engine (roughly done)

For a bet such as "will NVDA hit $164 this week", the engine:
1. takes the implied volatility of the listed option at the bet's price ($164), from the first expiry at or after the bet's deadline;
2. computes the probability that the stock touches that price before the deadline, counting regular trading hours only, because only those count on Polymarket. How much of a stock's movement happens overnight rather than during the session is estimated from its past daily prices.

### Trading strategy (mostly not done)

The current strategy is simple. Every 15 minutes during the session it compares the engine's fair price with Polymarket's price, fee included, and takes one share of every bet that is worth at least 3c more than it costs and returns at least 8% (for bets above 90c, at least 2%). It skips bets on which we believe the engine is less accurate:
- in the first 30 minutes after the open;
- on a stock whose earnings report falls before the deadline;
- on prices more than 2.5 standard deviations away from the current price;
- when the nearest options around the bet's price are thinly traded (less than 5% of the volume of the busiest strike) or more than 3% of the price apart.

There is a sound reason behind each filter; none was picked from a backtest to report better results.

Every bet is held until it settles. Spreads on these markets are so wide that buying and selling early erases most of the expected value; the spread does not apply if we wait for settlement.

The harder problems are still open:
- **Correlation.** Most bets in a week are strongly correlated, so even if we take only bets with positive expected value, there is a reasonable chance they all lose together. Waiting until settlement is the reason we have to worry about this.
- **Volume.** Most markets allow bets of only a few dollars, while some appear worth thousands. Taking the maximum possible on each would concentrate money in a few bets, which is risky. For now every bet is one share, so that every bet pays the same if it wins and no single bet decides a week.

## Testing

### Pricing engine

The engine was tested against option and price data from May 2018 to November 2026, on 19 stocks and ETFs, in two tests. Neither uses Polymarket; they check the engine against what prices actually did.

**Calibration.** Each sample took one point from every week, let the engine calculate the probability that the price touches a randomly chosen level, and checked whether it did. This test was run on 10 samples. Points from the same week are correlated, so the samples are not fully independent; the interval for all 10 samples combined comes from resampling whole weeks, which accounts for this correlation. Since a YES/NO outcome carries little information, this test has a wide error margin.

**PIT.** For the highest (and lowest) price the stock actually reached, the PIT asks the model how likely touching that level was. If the model is right, these probabilities are spread uniformly between 0 and 1, so every observation tests the whole distribution instead of giving a single yes/no. We also ran 10 PIT samples, again one point from each week.

We measured the correlation between samples with a one-way ANOVA by week: the calibration samples are practically independent (intraclass correlation +0.012, worth about 9 independent samples), while the PIT samples are not (+0.163, worth about 4), because in a week of large moves every stock goes further.

The finding is that the engine calculates probabilities very well, with a small bias towards overestimating touch probabilities, by 0.5–0.9 percentage points. This is not statistically significant. Two hypotheses tested so far are that these years were bullish, beating the risk-free rate by far (so the test itself is biased), or that option prices contain a risk premium because investors fear a crash. Further tests gave some support to the first idea, though it did not explain all of the bias, and did not support the second. I have chosen not to include a risk premium in the pricing engine, because the current risk premium is difficult to calculate, especially for single companies, and I believe it is small. I am not entirely sure how independent the tests are, and until I learn more about it, I don't think it is sound to simply make a small adjustment to the engine and test again.

Reports: [calibration](proof/past_data/historical/calibration/REPORT.md), [PIT](proof/past_data/historical/pit/REPORT.md), [correlation between samples](proof/past_data/REPORT.md#the-samples-are-correlated).

### Full model

**Replay.** The model was replayed over the Polymarket order books and option prices I scraped every 15 minutes, from 16 September to 13 November 2026. A bet counts only if the book showed that size at that moment, and every bet is settled on Polymarket's own result. The pricing is as of commit `4146d89`; a rerun on 20 November with the current code gave the same bets, cost and profit in every week. Result: 625 bets, a profit of $29.58 on $297.42 staked (9.9%), against $37.33 of expected value promised at entry. 327 bets won where the model expected 334.7, and 1 of the 9 weeks lost money (−10.0%); weekly returns ranged from −10.0% to +31.2%. [Report](proof/past_data/replay/REPORT.md), [weekly results](proof/past_data/replay/PERFORMANCE.md).

**Random control.** The same nine weeks with 625 bets chosen at random instead of by the model (random snapshot, market and side), repeated 2,000 times, lost $54.83 on average, and none of the 2,000 runs matched the model. Taking the model's own bets but choosing the side at random lost $26.63 on average, again with no run matching the model.

**Live.** Since 18 November 2026 the model runs during every session, identifies mispricings in real time and writes down every bet beyond the threshold before it settles. Since these are pushed in real time (which can be checked from the commit history) by a model fixed in advance, there is no data leakage. [Bets](proof/live_data/BETS.md), [settlements](proof/live_data/SETTLEMENTS.md), [weekly results](proof/live_data/PERFORMANCE.md).

## Caveats

- **Few independent weeks.** Bets within a week are strongly correlated, so 9 weeks of replay are closer to 9 independent points than to 625. That is not enough to draw a statistically significant conclusion yet. The random control shows the model's choices are far from random, not how certain its edge is in the future.
- **The replay is not blind.** Some decisions about the model were made knowing the previous results. Having realised we don't have enough independent data to make data-driven decisions, I try to keep every decision textbook, researched and known.
- **Live is short.** The live record covers one week so far.
- **Engine bias.** The engine's small bias towards overestimating touch probabilities is not explained yet (see [Testing](#pricing-engine)).
- **Paper only.** One share is below Polymarket's $1 minimum, so the strategy has not been run with real money.
- **Narrow scope.** Only weekly hit markets on single stocks and ETFs are tested; no index and no monthly markets.

## Plans

I consider the current phase a sufficient proof of concept: despite the approximations, the results so far suggest the idea works on real data. What the project needs now is a sounder design, so in December, after my first-semester finals, I plan to take a careful approach, evaluating each step of the pipeline and then:
- building a better trading strategy: every bet says a stock will go up or down, so a smart balance of UP and DOWN bets should cancel out much of the market movement and leave mostly the return from mispricing. We can go further by also balancing bets on a volatile against a calm week, or by modelling where the stock might go;
- expanding to other option-like market types.

## Where to find things

| Path | Contents |
|---|---|
| [`proof/past_data/`](proof/past_data/) | Replay, random control, calibration and PIT tests |
| [`proof/live_data/`](proof/live_data/) | Live bets, settlements and weekly results |
| `model/` | Pricing engine, strategy, replay |
| `data/` | Scraping and storage of Polymarket and option data |

## History and use of AI

I first realised in January 2026 that Polymarket bets carry some resemblance to options, and wrote a short script that took tables copy-pasted by hand from the Nasdaq website and Polymarket. It identified some mispricings, but it relied on a one-time copy-paste. In September, using Claude Code, I took a more structured approach, scraping both platforms every 15 minutes during open sessions to collect a large amount of data.

All of the code was written by AI (Claude Code), and I can assure very little about its quality. The live reporting is partly there to show that the project works despite potentially poor code. Initially I let Claude make many decisions without realising how far real bets are from an ideal 1-to-1 option equivalent, which resulted in a lot of mistakes. Now I am more careful: I research and make all the important decisions myself, such as which markets to target, how to price them, which models to test and how to evaluate them.

This README was reorganised and written up by AI (Claude) from my rough note, after a discussion about its structure and content.
