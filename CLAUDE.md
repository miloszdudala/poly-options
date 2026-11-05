# poly-options

Compare what the option market implies about a stock (or index, or crude)
event with what Polymarket and Kalshi charge for the same event. Assume the
option market is right, buy the mispriced side, hold it to resolution. Paper
money now; real money (starting at $50-100) later.

## What this project is not

- **Not a modelling project.** Fair value comes from listed option prices and
  textbook formulas (Black-Scholes and the one-touch formula). No home-made
  fair values, no fitted factors, no historical-frequency tables, no machine
  learning. If something needs an estimate the option market does not give,
  stop and ask; do not invent one.
- **Not a round-trip trader.** Polymarket spreads (~7c) eat any buy-then-sell.
  Every bet is held to resolution.
- **Not a judge of option pricing.** "The options are mispriced in one
  direction" is out of scope and may not justify any rule.

## Rules for every change

1. **Keep it small.** The repo should only collect data, simulate models and
   report. Delete code no current model, report or test reads; git keeps the
   history. Do not add features, options or abstractions nobody asked for.
2. **A refactor must not change results.** Run the simulation before and after
   on the same dates; ledgers must match. A change that is meant to move
   results goes in its own commit, separately from refactoring.
3. **Do not decide for the user.** Strategy, thresholds, caps and which markets to
   trade are the user's. Offer options; do not pick.
   Anything listed under "Open" below stays open until the user decides it.
4. **Mark unapproved claims.** A takeaway in a doc that the user has not
   approved is labelled *(unapproved)*. Never build on an unapproved takeaway.
5. **No side preference.** At equal computed edge, YES and NO are equal. No
   rule may rest on "NO did better last month".
6. **Never touch credentials.** No session gets Polymarket keys, and no code
   path in `model/` may place an order.

## Decided

- **Pricing a hit (one-touch) market:** touch probability over **regular hours
  only** (the market's own rule), with the **at-the-money implied vol** of the
  first expiry at or after the deadline. The split of variance between session
  and overnight (`f`) comes from each stock's past daily bars: the one accepted
  exception to "option prices only", decided 2026-11-05. Scored on 9,272
  observations of markets past their deadline: regular hours + ATM Brier 0.0788,
  regular hours + barrier-strike vol 0.0792, continuous + ATM 0.0799, continuous
  + barrier-strike vol 0.0811. Earlier scores included unfinished markets, which
  only appear as resolved once touched, and are not comparable.
- **Fills:** a fill counts if the book had the size when we looked. The ~40 s
  recheck showed 80-88% of filled dollars still buyable, so there is no
  separate "confirmed" ledger.
- **Accounts:** one account runs routinely for every model and type: **max
  bets** - no bankroll ceiling, no per-market cap; every bet is as large as
  the order book allows while it still clears the bar. Other accounts ($100
  compounding, fixed budget per period) run on demand only.
- **Types are independent.** Each type of bet (venue x market kind x period x
  asset class, e.g. Polymarket weekly hit on stocks, Kalshi daily S&P close,
  Polymarket monthly hit on WTI) has its own book and history and can be added
  or dropped without touching the others. Performance is reported per type,
  against the EV promised at entry.
- **Hedging** means taking other-side bets that are themselves above the bar,
  never paying for insurance. Two hedged books will exist: balanced on
  direction (up vs down), and on direction plus volatility (calm vs wild,
  roughly NO vs YES). Polymarket/Kalshi bets only.
- **WTI:** priced from USO options, vol converted at the same moneyness, with
  the contract-switch (roll) gap removed. Kalshi: kept and simulated.
- **Reporting:** a local HTML dashboard, regenerated whenever it is opened.
- **Real-money trading:** `live/` was removed on 2026-11-05 until there is
  confidence in the results; the last commit with it is `251105b`. When trading
  returns it must behave the same as the simulation.

## Focus (2026-11-05)

Only `edge_taker` on Polymarket hit markets is being developed. Hedged books,
other new models and the bar are parked. A cleanup may simplify anything but must
roughly reproduce, on the baseline dates, the settled result of `edge_taker`,
account `max`: weekly 855 bets, $100,113 in, $8,028 promised, PnL $5,384; monthly
379 bets, $23,759 in, $1,437 promised, PnL $3,322 (a local check; needs the store).
The other types (close-above, range, up-down, Kalshi, WTI) lose money or are not
believable yet: leave them out of a cleanup's reasoning, delete only on request.

## Formats

A new model or a new type of bet is one new file, and no other file changes.
A model (`model/models/`) receives this cycle's offers and what it holds, and
returns orders. It does not know its account or where data comes from. A type
(`model/types/`) says which markets belong to it, how to price one and its fee.
Accounts belong to the harness. `PLAN.md` T3 has the details.

## Open (do not decide these)

- The design of the two hedged books, including how much imbalance to allow.
  The user designs this and writes them as model files after the refactor.
  No task waits for it.
- The bar a bet must clear. Today: EV >= 3c a share **and** return >= 8%;
  above a 90c ask, return >= 2%; every YES charged a 2c risk premium first.
  Only the risk premium was approved (2026-10-09). Each model declares its
  bar; keep today's unchanged until the user decides.
- Whether gold, silver and the dollar index get the same conversion as WTI.

## Layout

- `data/` - collector. `data/src/jobs.py discover | snapshot | daily`. Writes
  `data/store/` (Parquet, ~2 GB, **not in git**).
- `model/` - pricing (`engine.py`, `fair.py`), simulation (`sim.py`), models
  (`models/`), reports. `model/run_all.py` runs every model; writes
  `model/store/` (not in git) and `model/reports/`.
- `scheduling/` - launchd jobs for the collector and the daily report.

## Commands

```bash
.venv/bin/python -m pytest data/tests model/tests -q
.venv/bin/python model/run_all.py --dates 2026-09-16,... --tag name
```

Tests that read the store skip when it is absent (as in a cloud session).
A cloud session therefore cannot run the simulation; any task whose "done
when" needs real results is checked locally before merging.
