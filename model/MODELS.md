# Models and types: what runs, and how it has done

What every model shares (the fair values, the fills, the accounting, the
data) is in `INSIGHTS.md` §1–4 and §6. Code comments citing "MODELS §3.x" or
"MODELS 4.x" mean those sections there.

## The formats

- **A model** is one file in `model/models/` (`models/base.py` has the
  format). It declares a `bar` and writes `decide(offers, book)`: `offers` is
  every bet of one type that clears the bar this cycle, best return first;
  `book` is what it holds (open and settled, dollars per direction and per
  vol, the cash its account allows). It returns orders. It never knows its
  account or where the data comes from.
- **A type** is one file in `model/types/` (`bet_types.py` has the format):
  which markets belong to it, how to price one, its fee, its period.
- **Accounts** belong to the harness (`sim.py`): `max` (no ceiling, the
  routine one), `compound` (`--start`, default $100, reinvests what comes
  back) and `budget` (`--per-period` per day, week or month of the type; no
  roll-over). Each model × type × account is its own replay.
- **The bar** (`models.TODAYS_BAR`): EV ≥ 3¢ and return ≥ 8%; above a 90¢
  ask, return ≥ 2%; every YES charged a 2¢ risk premium first. Open in
  `CLAUDE.md`: only the premium is approved (2026-10-09). Each model declares
  its bar.
- **Run:** `model/run.py` (any model × type × account × dates; prints, writes
  only with `--save`), `model/run_all.py` (every model on every type under
  `max`, saved), `model/daily.py`, `model/dashboard.py`.

## Types

| type | markets | priced by |
|---|---|---|
| `poly-hit-weekly-stock`, `poly-hit-monthly-stock` | Polymarket "hit $B" on stocks | continuous one-touch, ATM vol of the first expiry at or after the deadline (`engine.py`) |
| `poly-close-above-{daily,weekly,monthly}-stock` | "close above $K" | call spread at the expiry equal to the deadline |
| `poly-up-down-daily-stock` | "Up or Down"; strike = the previous trading day's close; "Opens Up or Down" skipped | call spread at that day's expiry |
| `poly-range-weekly-stock` | "close at $L–$H" | difference of two call-spread close-aboves |
| `kalshi-index-daily-sp500`, `kalshi-index-daily-ndx` | Kalshi KXINX/KXINXU, KXNASDAQ100/KXNASDAQ100U | SPX / NDX call spread at the expiry equal to the observation time |
| `poly-hit-monthly-wti` | Polymarket "WTI hit $B" | one-touch with USO vol at the same moneyness × the WTI/USO vol ratio, on the contract Active at the deadline |

A market whose deadline has no matching expiry is skipped and counted, never
interpolated. The WTI type prices nothing until the WTI/USO vol ratio is set
(`model/types/_wti.py`) and the collector stores the next contract's price.

## Models

### `edge_taker`

Takes every offer that clears today's bar, as large as the book allows. No
portfolio rule: it measures whether the mispricing exists and survives
costs. Its concentration and one-sidedness are measurements, not
recommendations.

### `random_control`

No signal: 12 bets a cycle drawn uniformly from every priced bet on offer
(bar: none), at most 2¢ above the best ask's all-in cost, as large as the book
allows within that. If it earns what `edge_taker` earns, the window drifted
and nothing here has shown an edge (INSIGHTS §4.5).

### Hedged books

Two hedged books (direction; direction and vol) are the user's to design and
will be written as model files. Their predecessors, `direction_balanced` and
`scenario_hedged`, were deleted on 2026-11-04 (git history has them), as were
`factor_neutral`, `balanced_book` and `empirical_longshot` on 2026-10-17.

### Not built

- `maker` (resting bids): in a book this thin a resting bid is mostly filled
  by someone who has just seen the stock move against it; doing it honestly
  needs the trade tape, a cancel rule and a queue model.
- Selling before resolution: every bet is held to resolution (`CLAUDE.md`).
- Kalshi year-end index ladders: no type yet.

## Results so far (historical, under code that no longer exists)

The numbers below come from the `run_all.py` run of 2026-10-09, under the
pricing and accounts before the 2026-11-04 refactor: the regular-hours Monte
Carlo, `min(fair_K, fair_atm)` for YES and `max` for NO, a $10,000 bankroll
with $150 YES / $500 NO per-market caps, and an optimistic / confirmed split
of fills. They cannot be reproduced with the current code (git history can),
and two of the models below are deleted. The 2¢ YES premium applied.

September-deadline markets, all resolved, optimistic fills:

| model | positions | cost | PnL | ROI |
|---|---:|---:|---:|---:|
| edge_taker | 180 | $15,652 | +$4,030 | +25.8% |
| direction_balanced | 168 | $10,783 | +$3,483 | +32.3% |
| scenario_hedged | 162 | $11,826 | +$2,846 | +24.1% |
| random_control | 111 | $12,740 | −$1,270 | −10.0% |

edge_taker before the premium: September NO +$5,062, YES −$1,666; its
largest single loss was 33 two-day YES bets on the September monthlies on
09-28, −$1,590. The random control lost about two-thirds of its YES cost and
broke even on NO.

Takeaways drawn at the time, none approved:

- The option signal is good on NO and bad on YES *(unapproved)*.
- Most of edge_taker's NO profit came from cheap NO (bought under 50¢ where
  the market priced a touch at 75–85%) *(unapproved)*.
- A hedged book that balances direction with NO on both sides does not hedge
  a big move: NO on both sides of a stock loses whichever way it jumps
  *(unapproved)*.
- Volatility, not direction, is the common factor across markets: touches
  came in at 0.72× the model's rate in the week of 09-21 and 1.59× in the
  week of 10-12, and same-ticker bets win or lose together *(unapproved)*.
