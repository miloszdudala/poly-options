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
3. **Do not decide for the user.** Strategy, thresholds, caps, which markets to
   trade and anything in `live/` are the user's. Offer options; do not pick.
   Anything listed under "Open" below stays open until the user decides it.
4. **Mark unapproved claims.** A takeaway in a doc that the user has not
   approved is labelled *(unapproved)*. Never build on an unapproved takeaway.
5. **No side preference.** At equal computed edge, YES and NO are equal. No
   rule may rest on "NO did better last month".
6. **Never touch credentials.** `live/` trades real money; no session gets
   Polymarket keys, and no code path in `model/` may place an order.

## Decided

- **Pricing a hit (one-touch) market:** continuous one-touch formula with the
  **at-the-money implied vol** of the first expiry at or after the deadline.
  Scored against 7,750 resolved observations (2026-11-03): ATM Brier 0.0912,
  barrier-strike vol 0.0922, rule of two 0.1033 (exact-expiry rows only),
  highest-volume option's vol 0.1136. No regular-hours correction: it moved
  fair by a median 1.4c and did not improve the score (0.0941 vs 0.0945). A
  marked comment in the pricing code says where it would go.
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
- Whether `live/pricing_engine/` stays a separate copy of `model/` code.

## Layout

- `data/` - collector. `data/src/jobs.py discover | snapshot | daily`. Writes
  `data/store/` (Parquet, ~2 GB, **not in git**).
- `model/` - pricing (`engine.py`, `fair.py`), simulation (`sim.py`), models
  (`models/`), reports. `model/run_all.py` runs every model; writes
  `model/store/` (not in git) and `model/reports/`.
- `live/` - the real-money trader. Dry by default. Independent of `model/`.
- `scheduling/` - launchd jobs for the collector and the daily report.

## Commands

```bash
.venv/bin/python -m pytest data/tests model/tests live/tests -q
.venv/bin/python model/run_all.py --dates 2026-09-16,... --tag name
```

Tests that read the store skip when it is absent (as in a cloud session).
A cloud session therefore cannot run the simulation; any task whose "done
when" needs real results is checked locally before merging.
