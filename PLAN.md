# Refactor plan

One cloud session does all the tasks, in this order: T1, T2, T3, T4, T5, T7, T8.
Read `CLAUDE.md` first; its rules apply to every task.

**How to work through them:**
- Setup first: `pip install -r requirements.txt -r live/requirements.txt`.
- One branch, one pull request at the end, and **exactly one commit per task**,
  with the task number first in the message ("T1: ..."). The user's machine
  checks each commit separately against the baseline, so a commit must not mix
  tasks, and T1 must not change results.
- Run the tests before each commit. A task must not add failures.
- Do only what each task says. If a task needs a decision listed as "Open" in
  `CLAUDE.md`, or cannot be finished, skip that part, say so in the commit
  message and in the PR, and carry on with the next task. Do not stop to ask,
  and do not guess.
- Do not rerun or hunt for things you cannot check: there is no data here.

A cloud session has the code but not the data (`data/store/`, `model/store/`
are not in git). It can run the unit tests, not the simulation. "Checked
locally" below means the user's machine reruns `model/run_all.py` on the
baseline dates and compares with the baseline, after the session.

**Baseline:** commit `0f94b99`, dates 2026-09-16 .. 2026-10-30, run with
`--tag baseline` (ledgers in `model/store/ledger/*_baseline/`, kept locally).

**Known test state at the baseline:** 524 pass, 3 fail, none caused by a task:
`test_bar_spot_agrees_with_cboe_s_delayed_price` (one EWY snapshot on
2026-11-02, a data check that skips without the store);
`test_the_engine_copy_matches_model[data]` (`live/pricing_engine/data.py` has
drifted from `model/data.py`; Open in `CLAUDE.md`, leave it);
`test_live_never_reaches_into_model` (passes alone, fails only when the three
suites run in one process). A task must not add failures.

---

## T1 - Delete dead code

Results must not change.

Delete:
- The empirical fair value: `fair_empirical` in `model/engine.py`,
  `model/fair_schema.py`, `model/opportunities.py`, `model/run_fair.py`,
  `model/sim.py` (including the `fair_source == "empirical"` branch in
  `model_fair`), `model/calibration.py` and `model/run_calibration.py` if
  nothing else imports them, and `TouchTable` / `touch_table` in
  `model/empirical.py`. Keep `ewma_vol` and `intraday_variance_share` for now
  (T2 removes the last use of `f`).
- Traces of retired models (`empirical_longshot`, `balanced_book`,
  `factor_neutral`) anywhere in code, tests or docs.
- `model/experiments/` scripts whose results are already written into docs.
- The optimistic/confirmed split in reports and summary tables: keep the
  optimistic figures only (decided; see `CLAUDE.md`). Keep the recheck data
  collection in `data/`.

Do not touch `live/`, `data/`, or the three models' logic.

Done when: tests pass (no new failures), `grep -ri "empirical\|longshot\|confirmed"`
over `model/` finds only what is deliberately kept, and the PR lists every
deleted file and why. Checked locally: ledgers identical to the baseline
(apart from the removed confirmed columns).

## T2 - Pricing: ATM vol, continuous one-touch

Results change on purpose.

- Hit markets: fair = `fair.one_touch` (continuous) with the at-the-money
  implied vol of the first expiry at or after the deadline. Remove the
  barrier-strike vol, the two-expiry interpolation if ATM no longer needs it,
  the regular-hours Monte Carlo (`touch_probability`, `touch_delta`, the
  normal pool) and `f`. Remove `model/empirical.py` if nothing is left in it.
- Replace the current min/max rule (`min(fair_K, fair_atm)` for YES,
  `max(...)` for NO) with the single ATM fair.
- Leave one marked comment where a regular-hours correction would go, with
  the measurement: median 1.4c, p90 5.3c, Brier 0.0941 vs 0.0945, 3,326
  markets, 2026-10-30.
- Remove columns from the fair table that no longer exist.

Done when: tests pass, rewritten where they tested removed code; `fair.py` and
`engine.py` are shorter. Checked locally: rerun, and report how many bets
changed and the PnL difference against the baseline.

## T3 - One format for models, accounts, on-demand runner

The goal: a new model or a new type is **one new file** and nothing else. The
harness finds it, runs it and reports it. Existing code changes for neither.

**Model format.** One file in `model/models/`:

```python
class EdgeTaker(Model):
    name = "edge_taker"
    bar = TODAYS_BAR      # the bar this model's bets must clear

    def decide(self, offers: list[Bet], book: Book) -> list[Order]:
        return [Order(bet, usd=bet.max_usd) for bet in offers]
```

- `offers`: every bet of this type that clears `self.bar` this cycle, best
  return first.
- `Bet` carries everything a model may need, and nothing specific to one type:
  `market_id`, `ticker`, `type`, `side`, `win_prob`, `value`, `ev`, `roi`,
  `deadline`, `max_usd` (the most of the book that still clears the bar),
  `direction` (`up`, `down` or `None`) and `vol` (`calm`, `wild` or `None`).
  For a hit market: YES on an up barrier and NO on a down barrier are `up`;
  the other two are `down`; NO is `calm`; YES is `wild`.
- `book`: what the model already holds, with dollars per direction and per
  vol, cash available under the current account, and settled results.
- A model never knows which account it runs under or where data comes from.
  It returns orders; the harness caps them by `max_usd` and by cash, fills
  them and does the accounting.
- `TODAYS_BAR` holds today's bar unchanged (EV >= 3c and return >= 8%; above a
  90c ask, return >= 2%; YES charged 2c first), in one place. The bar stays an
  Open decision: this task moves it, it does not change it.

**Type format.** One file in `model/types/`: its name, which markets belong to
it, how to price one, and its fee. T3 writes the two that exist today,
Polymarket hit weekly and monthly on stocks, in this format.

**Models after this task:** port `edge_taker` and `random_control` to the
format. Delete `direction_balanced` and `scenario_hedged`: the user will write
their replacements as model files, and git keeps the old ones. A test must
define a throwaway model in the test file and run it through the harness, to
prove the format is enough.

**Accounts** belong to the harness, not to models:
- Remove the $10,000 bankroll and the $150 YES / $500 NO per-market caps, and
  the ticker and direction caps.
- `max` (the routine one): no ceiling; capital used = the sum of bets.
- On demand only: `compound` (starts at `--start`, default $100, reinvests
  settled cash) and `budget` (`--per-period` dollars each period; weekly types
  get a weekly budget, monthly a monthly one, daily a daily one; unspent budget
  does not roll over).

**Runner.** One command, any model x type x account x dates:

`python model/run.py --model edge_taker --type poly-hit-weekly-stock --account compound --start 100 --from 2026-10-01 --to 2026-10-31`

Defaults: all models, all types, account `max`, all dates. Prints a short
result (bets, money in, promised EV, PnL, PnL by week) and writes nothing
unless `--save`. `model/run_all.py` and the daily report go through the same
code. Every report shows **promised EV at entry against realised PnL**.

Each model x type is simulated separately, so one type's capital never
competes with another's.

Done when: tests cover the throwaway model, each account on a small synthetic
book (sizes, compounding, budget reset), and adding a type or model without
editing any other file. Checked locally: `max` on edge_taker reproduces T2's
bets with only the caps removed.

## T4 - New types priced directly from the chain

Add these Polymarket types, one file each, in T3's format, priced **directly
from the option chain** (no touch formula): `close_above` (daily, weekly,
monthly), `up_down` (daily; strike = that day's open), `range` (weekly;
difference of two close-aboves). `direction` as for any bet that pays when the
price rises or falls; `vol` is `None`.

Price "finishes above K" from the call spread around K at the expiry equal to
the market's deadline. **If no expiry matches the deadline exactly, skip the
market** (do not interpolate) and count the skips in the report.

Done when: tests price each new type on a synthetic chain with a known answer
(call spread on a flat-vol chain = N(d2)), and no file outside `model/types/`
changed except tests. Checked locally: the new books run and the hit types
still reproduce T3.

## T5 - New sources: Kalshi and WTI

- **Kalshi** (types in T3's format; `data/store` already holds markets and books for the series in
  `data/config/markets.yaml`): types for the S&P 500 and Nasdaq 100 "above X /
  in range at time T" series, priced from the SPX / NDX call spread at the
  matching expiry (same skip rule as T4). Kalshi fees: check the current fee
  schedule in Kalshi's docs and cite it in the code. Note in the report that
  Kalshi books are collected hourly and option prices are 15 minutes delayed,
  which matters for hourly markets.
- **WTI** hit markets (resolve on ICE WTI "Active Month", Pyth `CLL`; the
  Active Month switches at the open of the contract's third-to-last session):
  vol from USO options at the same moneyness, scaled by the WTI/USO
  realised-vol ratio (measured 2026-10-27: median 1.06-1.09). Remove the roll
  gap using the next contract's price (yfinance `CL<month><yy>.NYM`, e.g.
  `CLF27.NYM`). Touches count in ICE trading hours, not stock hours.
- Do not add gold, silver or the dollar index (Open).

Done when: tests cover the Kalshi fee and payoff, the roll adjustment and the
moneyness conversion on synthetic data. Checked locally.

## T6 - Hedged books (not a cloud task; the user writes them)

Written later as model files in T3's format, once the refactor is done. Nothing
in T1-T8 waits for them. Every bet, hedges included, must clear the same bar.
No fitted risk model.

## T7 - Dashboard

A single local HTML file generated from the ledgers, no server:
- one row per model x type: bets, money in, promised EV, realised PnL,
  realised / promised, number of settled weeks;
- per type: PnL by week (weekly types) or month (monthly types), with the
  promised EV alongside;
- drill-down to individual bets;
- filters by model, type, date range.

Done when: it renders from a small fixture ledger in the tests, opens with no
network, and the PR includes a screenshot.

## T8 - Docs check (last)

For `README.md`, `MARKETS.md`, `FINDINGS.md`, `model/MODELS.md`,
`model/INSIGHTS.md`, `live/README.md`, `model/docs/models_explained.html`:
- delete what describes removed code;
- check every factual claim against the code; fix or flag it;
- label every takeaway not listed as decided in `CLAUDE.md` *(unapproved)*.
Keep the files separate.

Done when: the PR lists each claim changed or labelled, with a one-line reason.

## T9 - The bar (not a cloud task; the user decides)

Nothing waits for this: each model declares its bar, so changing it later is
one line.
