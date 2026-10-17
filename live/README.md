# live: trading the signal with real money

A small live test of the option-implied signal on Polymarket's stock "hit"
markets: a $50 book, $5 a market, held to resolution. It runs only while you
have a `loop` running in a terminal. Nothing schedules it.

**Dry by default.** A real order needs all three: `--live` on the command,
`armed: true` in `config.yaml`, and a connection Polymarket does not block.

## Not decided yet: these are yours to choose

Everything below was filled in by Claude on 2026-10-17 so the code would run.
None of it is your decision yet. Go through it before December, and before
any real money.

| what | placeholder now | where |
|---|---|---|
| strategy | `edge_taker`'s rule only: every edge, best ROI first. No option for `direction_balanced` or `scenario_hedged` | `strategy.py`, `trader.py` |
| EV bar | 6¢ (`min_ev`) | `config.yaml` |
| ROI bar | 8% (`min_roi`) | `config.yaml` |
| above 90¢ | a 2% ROI bar replaces the EV bar, so NOs at 92–95¢ with 2–3¢ of edge pass (the dry runs bought exactly these) | `config.yaml` |
| YES risk premium | 2¢ | `config.yaml` |
| fee used to decide | the replays' `0.04 × min(p, 1−p)`, not the venue's lower `0.04 × p(1−p)` | `fee_model` |
| money | $50 at risk, $5 a market, $20 a day | `config.yaml` |
| pace | one order a minute; a cycle every 60 s | `config.yaml` |
| re-entry | never: one entry per market, ever | `ledger.held_markets` |
| depth | at most half of any price level | `take_fraction` |
| order type | taker only, fill-and-kill; no resting orders | `venue.buy` |
| exits | none: hold to resolution | – |
| between snapshots | fair moved along delta; a ticker that has moved more than 1% sits out; snapshots older than 20 min are not traded | `config.yaml` |
| data | the loop starts the collector itself when no snapshot is newer than 16 min | `config.yaml` |

## Before anything else: where you are

Polymarket refuses new orders from Singapore, Thailand, Poland, the US, the
UK, France and others ("close-only", checked live on 2026-10-17). From this
Mac in Singapore, `https://polymarket.com/api/geoblock` answers
`{"blocked": true, "country": "SG"}`. Every live cycle asks that endpoint and
refuses to trade unless it says `blocked: false`. Do not route around it with a
VPN: it breaks Polymarket's terms (accounts can be frozen, funds included) and
local law. Polymarket not blocking a country is not the same as that country
allowing it; Indonesia, for one, blocks gambling sites itself, so that check is
yours to make.

## Set up, once, from somewhere allowed

```bash
cd ~/Code/poly-options
.venv/bin/pip install -r requirements.txt -r live/requirements.txt
cp live/.env.example live/.env        # gitignored
```

1. **A fresh wallet for this alone.** Create a new account in a browser
   wallet (Rabby or MetaMask), and put nothing else in it. Export its private
   key into `live/.env` as `POLYMARKET_PRIVATE_KEY`.
2. **Fund it through polymarket.com.** Connect that wallet on polymarket.com
   and deposit $50 there. The site converts USDC into pUSD, the collateral
   Polymarket has used since its V2 exchange went live on 2026-04-28. An
   API-only wallet would have to do that conversion by hand.
3. **Check:** `.venv/bin/python live/trader.py check`. It must show the
   wallet you funded and a ~$50 balance. If it shows a different wallet
   with $0, the site gave you a different deposit wallet: copy the address
   polymarket.com shows into `POLYMARKET_WALLET_ADDRESS` and check again.
4. **Approvals:** `.venv/bin/python live/trader.py setup --live` approves
   the exchange contracts. It writes to the chain but spends no collateral;
   the SDK pays the gas on its gasless wallets.
5. **Arm:** set `armed: true` in `live/config.yaml`.

## Every trading day

The Mac must be awake and online for the whole session, as for the collector
(root README).

```bash
.venv/bin/python live/trader.py check          # READY?
.venv/bin/python live/trader.py loop           # dry: decides, records, sends nothing
.venv/bin/python live/trader.py loop --live    # real orders
```

`loop` runs one cycle a minute. Started before the open, it waits for it, and
it stops by itself at the close. To stop it early, press Ctrl-C, or run
`touch live/STOP` from anywhere; the kill switch also blocks every later run
until you delete the file. Each minute prints one line:

```
11:47:02 ET  20261016T163005Z (3.9 min): 566 markets, 5 candidates, 1 orders $5.00; skipped {...}
```

Afterwards:

```bash
.venv/bin/python live/trader.py status         # paper and live books, wallet, positions
.venv/bin/python live/trader.py redeem --live  # turn resolved winning shares back into cash
```

Run a dry week first. Dry runs keep their own paper book with the same caps,
so the week shows what would have been bought and what it would have cost.

## What one cycle does

1. **Preflight.** The kill switch, and for `--live`: armed, key present,
   geoblock clear, account not in closed-only mode.
2. **Snapshot.** The newest snapshot the collector wrote (books plus option
   chains, every 15 minutes) is priced once by the engine, in ~10 s. Older
   than `max_cycle_age_minutes` means no trading. Older than
   `collect_if_older_than_minutes` means the loop starts the collector
   itself, so it does not need the launchd jobs installed.
3. **Now.** Today's 5-minute bars from yfinance. Each fair value is moved
   along its delta to the current spot. A ticker that has moved more than
   `max_spot_move` since the snapshot sits out until the next one.
4. **The bar.** Live order books for every priced market; the same test the
   replays use (`strategy.py`): EV ≥ `min_ev` and ROI ≥ `min_roi` after fees,
   YES charged a 2¢ risk premium, cautious fair per side.
5. **Each candidate,** best ROI first, at most `max_orders_per_cycle`:
   - skip any market already bought (one entry per market, ever);
   - skip it if the barrier has printed since the snapshot, counting the bar
     still forming;
   - size it within all of: $5 a market, $50 at risk, $20 a day, the wallet;
   - re-read its book, walk the asks up to the limit, never take more than
     half a level, and require Polymarket's 5-share minimum;
   - send a **fill-and-kill** buy: whatever does not fill at once is
     cancelled, so nothing ever rests in the book.
6. **Record** it in `live/store/` (below).

## Files

| file | what |
|---|---|
| `trader.py` | the commands, the loop, the per-cycle decision |
| `strategy.py` | the bar and the ladder walk (pure functions) |
| `pricing.py` | snapshot → fair values, bars, delta move, collector start |
| `pricing_engine/` | a frozen copy of the fair-value engine (its own README) |
| `venue.py` | the only file that talks to Polymarket (`polymarket-client` 0.13.0) |
| `preflight.py` | the checks before any order |
| `ledger.py` | the record, append-only JSON lines |
| `settings.py`, `config.yaml` | limits and bars; secrets come from `.env` only |
| `store/runs.jsonl` | one line per cycle: snapshot, counts, why things were skipped |
| `store/orders.jsonl` | one line per order: the decision and, for live, the venue's answer |
| `store/settled.jsonl` | conditions redeemed |
| `store/collector.log` | output of any snapshot the loop started |

## How it stands apart from `model/`

`live/` imports nothing from `model/`, and nothing outside `live/` imports
`live/`; the tests check both. The one thing it shares with the replays is
the collector in `data/` and its store, which both read and neither owns.
`pricing_engine/` is a copy of the model's engine, and a test fails when
the two differ, so taking a later engine fix is a deliberate step (see its
README). `strategy.py` is a copy of
`sim.candidate_edges` and `sim.walk_ladder` for the same reason.

As copied, live differs from the replays in only these ways: flat $5 caps
instead of $150 YES / $500 NO, no re-entry, the venue's 5-share minimum, and
fair values moved to the current spot between snapshots.

## Not yet known, and how you will find out

- **Fill units.** The amounts an accepted order reports (`makingAmount`,
  `takingAmount`) have not yet been seen on a real fill. If one reports more
  than 5% over what was asked, the trader writes `live/STOP` and halts.
  Check the first live fill in `store/orders.jsonl` by eye.
- **The wallet the API trades from.** See step 3 of setup.
- **The fee.** The venue charges `0.04 × p × (1−p)` per share. Decisions use
  the replays' `0.04 × min(p, 1−p)`, which is higher (2× at 50¢, 11% at
  90¢), so live clears the same bar the replays did. `fee_model: venue`
  switches to the real formula.
- **The EV bar.** `min_ev: 0.06` comes from the $100 test of 2026-10-15,
  which had a NO-only filter (since withdrawn). Re-run that test with both
  sides before arming.
## Tests

```bash
.venv/bin/python -m pytest live/tests -q
```

Run them on their own, not in the same pytest run as `model/tests`: both
sides have a module called `data`, and one process can hold only one.
