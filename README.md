# poly-options

Read-only data collection for comparing Polymarket (and Kalshi) contracts on US
stock prices with probabilities implied by listed options.

**This repository collects data only.** No accounts, no API keys, no orders.
Modelling and signal logic belong in `model/`, which is deliberately empty.

Because liquidity is thin, the **full order book** (every level, both sides, both
outcome tokens) is stored at every snapshot. Mid, last trade and best bid/ask are
never stored on their own.

## Install

```bash
cd ~/Code/poly-options
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run once, by hand

```bash
.venv/bin/python data/src/jobs.py discover    # find tracked markets (~60 s)
.venv/bin/python data/src/jobs.py snapshot    # books now (+ options in US hours)
.venv/bin/python data/src/jobs.py daily       # rates, bars, dividends, earnings
```

`snapshot` and `daily` skip themselves outside their cadence (off-hour ticks,
non-trading days). Add `--force` to run anyway.

## Install the schedule

```bash
./scheduling/install.sh              # install or refresh
./scheduling/install.sh --uninstall  # remove
```

The script detects the repository path and converts the daily job's 17:30
`America/New_York` slot into this machine's local wall-clock time, because
launchd calendar entries are local. **Re-run it after a US DST change**
(1 November 2026) so the daily job stays after the US close.

Cadence:

| when | what |
|---|---|
| every 15 min, 09:30–16:00 ET on NYSE sessions | Polymarket books, option chains, spot |
| hourly within the session | Kalshi books (distant expiries; they do not move faster) |
| outside the session | **nothing** |
| 17:30 ET, trading days | discovery, metadata, rates, dividends, earnings, bars, price history |

Nothing is collected outside the NYSE regular session. The venues do keep
trading overnight and at weekends, but no option chain is quoted then, so an
off-hours book row has nothing to be compared against - which is the only reason
this dataset exists. `schedule.collect_offhours: true` restores hourly off-hours
snapshots.

### The machine has to be awake for that window

launchd does not run a job while the Mac is asleep. It fires a missed
`StartInterval` job once on wake, so a sleeping laptop loses the session rather
than backfilling it.

| | local (UTC+8) |
|---|---|
| session | 21:30 → 04:00 |
| daily job | 05:30 |
| **awake window** | **21:30 → 05:30** |
| after the US falls back, 1 Nov 2026 | 22:30 → 06:30 |

Idle sleep is already off (`pmset -g custom` shows `sleep 0` on both AC and
battery), so in practice: **leave the lid open and keep it plugged in.** Closing
the lid sleeps the Mac regardless of that setting, unless it is in clamshell mode
on an external display. Wi-Fi has to stay up. Check that nothing was missed with
`data/inspect.py`, or by counting cycles in `runs` for the date.

Session boundaries come from the `pandas_market_calendars` NYSE calendar, so
half-days and US market holidays are handled, and the tz database supplies the
offset rather than a hard-coded one.

## Inspect

```bash
.venv/bin/python data/inspect.py            # latest cycle
.venv/bin/python data/inspect.py --cycle X  # a specific cycle
```

Prints per-table row counts for the latest cycle, the run summary, one example
order book, and any failures.

`inspect.py` sits outside `data/src/` on purpose: a module named `inspect.py`
shadows the standard-library module for every sibling that imports it (numpy and
duckdb both do), which would break the collectors if it lived next to them.

## How large a bet fits

The whole book is stored at every level precisely so this is answerable, and
**nothing in the collector averages anything** - no mid, no VWAP, no top-of-book
shortcut. Effective fill price for a given dollar size is a query, not a stored
column:

```sql
WITH w AS (
  SELECT market_id, ticker, strike, price, depth_rank,
         SUM(price*size) OVER (PARTITION BY market_id ORDER BY depth_rank) AS cum_cost,
         SUM(size)       OVER (PARTITION BY market_id ORDER BY depth_rank) AS cum_sh
  FROM read_parquet('data/store/parquet/poly_books/**/*.parquet')
  WHERE side='ask' AND outcome_index=0 AND contract_type='close_above'
)
SELECT ticker, strike,
  MAX(CASE WHEN depth_rank=0 THEN price END)                     AS top_ask,
  MIN(CASE WHEN cum_cost>=100  THEN round(cum_cost/cum_sh,3) END) AS px_100usd,
  MIN(CASE WHEN cum_cost>=1000 THEN round(cum_cost/cum_sh,3) END) AS px_1000usd
FROM w GROUP BY market_id, ticker, strike;
```

Measured on a September 2026 cycle: AAPL 370 `close_above` showed a 0.114 top
ask, 0.145 average for $100, and 0.591 for $500. Sizing is not a refinement of
the signal, it *is* the signal.

**Always key on `market_id`.** `(ticker, strike, contract_type)` is not unique -
MSFT `close_above` 510 is open in four months simultaneously - so grouping on
those three silently blends different expiries. `end_date` and `period` ride on
every book row to make the mistake visible.

### What the books actually looked like, 2026-09-16

| contract_type | n | median spread | within 2¢ |
|---|---|---|---|
| hit_up | 288 | 0.070 | 16 |
| hit_down | 262 | 0.074 | 12 |
| close_above | 258 | **0.188** | 5 |
| range | 112 | 0.129 | 0 |

The `close_above` ladders - the original primary track - are now ~19¢ wide. The
one-touch `hit` markets are less than half that. On Kalshi the tight series are
the year-end ones (`KXNASDAQ100Y` 1¢, `KXINXY` 4¢, `KXNDQDIRY` 4¢), while the
big daily ladders run 20-31¢.

### Fees are not optional

Every tracked Polymarket market carries
`{rate: 0.04, exponent: 1, takerOnly: true, rebateRate: 0.25}`, stored per market
in `poly_markets.fee_schedule`. With exponent 1 the taker fee is
`0.04 x min(p, 1-p) x shares` - on a 20¢ contract that is 0.8¢/share, **4% of
premium**, and makers are rebated 25% instead. Any edge measured against a mid
price is measuring something untradeable: you pay the spread *and* the fee.

### Option quotes are ~15 minutes stale

Free Cboe quotes lag, so an option-implied probability compared against a live
prediction-market book mostly measures underlying drift. `option_quotes` keeps
`source_quote_time_utc` per contract and `underlying_quotes` keeps spot with its
own timestamp, which is what a model needs to delta-adjust the option forward to
the book's timestamp. Doing that correction is a **requirement in `model/`**, not
an optional refinement - skip it and your "edge" is a clock artifact.

## Query

Everything is Parquet, partitioned by date, readable straight from DuckDB:

```sql
SELECT side, depth_rank, price, size
FROM read_parquet('data/store/parquet/poly_books/**/*.parquet', hive_partitioning=true)
WHERE ticker = 'TSLA' AND contract_type = 'hit_up' AND barrier = 397.5
ORDER BY side, depth_rank;
```

`depth_rank = 0` is always top of book on both sides. The venue's own ordering is
kept separately in `level_rank` — the Polymarket CLOB returns both sides
worst-price-first, so reading `level_rank = 0` as the best price walks the book
backwards.

**Adding a column is a breaking change unless you backfill.** DuckDB's
`read_parquet` glob errors outright when one file in the tree has a column its
neighbours lack — not a null-filled column, a hard `schema mismatch in glob`. So
whenever a collector gains a field, rewrite the existing partitions with that
field as nulls (cast through `schemas.SCHEMAS[table]` so the types match exactly)
in the same change. `union_by_name=true` papers over it per query, but only for
queries that remember to say so.

### Tables

`poly_markets`, `poly_books`, `poly_price_history`,
`poly_unparsed`, `kalshi_markets`, `kalshi_books`, `option_quotes`,
`underlying_quotes`, `underlying_bars`, `rates`, `dividends`, `earnings`,
`resolutions`, `runs`, `failures`.

Every table carries `fetched_at_utc` and `cycle_id`. Source timestamps live in
their own columns (`book_timestamp_utc`, `source_quote_time_utc`,
`last_trade_time_utc`) and are never merged with our fetch time — free option
data is delayed ~15 minutes, which matters when aligning against a Polymarket
snapshot. Rows sharing a `cycle_id` were fetched in the same run.

Raw responses are archived verbatim, gzipped, at
`data/store/raw/{venue}/{date}/{cycle_id}_{identifier}.json.gz`.

## Configuration

- `data/config/markets.yaml` — endpoints, discovery patterns and blocklists,
  Kalshi series, cadence, HTTP retry/pacing.
- `data/config/underlyings.yaml` — underlyings by tier, per-source symbol
  spellings, expiry horizon.

Discovery is pattern-based rather than date-filtered, so new months are picked up
without code changes.

## Tests

```bash
.venv/bin/python -m pytest data/tests/ -q
```

Cover strike/barrier/type parsing (every string taken verbatim from the live
API), timestamp and timezone handling including the 1 November 2026 DST change,
order-book flattening, expiry selection, and the cadence guard.

## What it costs to run

Measured, not estimated. One snapshot during US hours writes ~3.5 MB; 26 of
those a day, 252 days a year. Nothing is written outside the session, so these
are whole-year totals:

| item | per year | why it is that size |
|---|---|---|
| `option_quotes` | ~19 GB | ~60k contracts/cycle: every strike and expiry within 45 days, for 25 underlyings |
| `poly_books` | ~5 GB | full depth, both outcome tokens, ~33k price levels/cycle |
| `kalshi_books` | ~2.6 GB | ~3.5k markets, one book each |
| `kalshi_markets` | ~1.1 GB | quotes, volume and open interest per market |
| everything else | ~0.5 GB | bars, rates, dividends, earnings, discovery, spot |
| **total** | **~28 GB** | |

A full cycle takes about 3 minutes: Polymarket books 13 s, Kalshi 142 s, options
23 s. Kalshi is the slow part because it only serves one order book per request.

Three settings move these numbers:

- `kalshi.book_filter` (markets.yaml) - depth is requested only for markets that
  have traded, are quoted within 20¢, or belong to a series we always price.
  Measured: 3,519 open markets -> 2,201 depth requests, ~142 s -> ~88 s. Top of
  book for *every* market is still stored, because `GET /markets` returns
  yes/no bid, ask and sizes for all of them in ~10 paged requests.
- `kalshi.interval_minutes` (markets.yaml) - `15` (every tick) or `60` (hourly).
  Hourly by choice, not by budget: the tracked Kalshi series are index digitals
  with distant expiries that do not move fast enough to repay a 15-minute
  sample. Every-tick would cost ~6.5 GB/yr against ~2.6 GB/yr hourly.
- `kalshi.book_workers` - **raising this does nothing.** Measured 2026-09-16:
  25.0 / 24.7 / 27.1 req/s at 8 / 16 / 24 workers. The ~25 req/s ceiling is
  Kalshi's, not ours, so the only way to spend less time is to send fewer
  requests.
- `options.strikes` (underlyings.yaml) - the core band around spot. Tightening it
  saves little: your own tracked levels span wide ranges (NFLX -74% to +80% of
  spot, Kalshi year-end SPX ladders 4,000-10,600), and the filter deliberately
  keeps every strike near a level you track.
- `raw.*` (markets.yaml) - all off by default except market rules. Archiving raw
  responses every cycle cost 54 GB/yr, 55% of the total, and no model reads it.

### Why these defaults

- **Raw archives are off.** `option_quotes` already stores 20 of the 23 fields
  Cboe sends, and the missing three are derivable. Market rules text *is*
  archived, but only once per market and again only if the venue changes it -
  that is the record to check a disputed settlement against.
- **yfinance runs daily, not per cycle.** Cboe covers all 25 tickers in 29 s and
  carries bid/ask sizes, greeks and per-contract quote times. yfinance needs a
  separate paced request per expiry - ~406 s for the same tickers - so it serves
  as a daily cross-check instead.
- **No 1-minute bars.** For deciding whether a barrier was touched, the high of a
  5-minute bar is by definition the maximum of the five 1-minute highs inside it,
  so aggregating loses nothing. 1-minute data is also a worse volatility
  estimator, because bid-ask bounce inflates it.
- **Bars are incremental.** The first run backfills 5 years; later runs fetch
  only new sessions (11,868 rows -> 4).
- **No separate last-trade table.** Each book payload carries its own
  `last_trade_price`, stored as `book_last_trade_price` on every level row.

## Notes on the data

- Polymarket equity markets resolve on **Pyth** prices, not the exchange
  official close. Monthly "hit" markets check the High/Low of Pyth 1-minute
  candles during regular hours only. Full rules text is stored verbatim per
  market in `poly_markets.rules_text`.
- Kalshi prices arrive as dollar strings (already probability units) in
  `*_dollars` fields, with sizes in `*_fp` fields. Both the normalised
  probability and the raw value are stored.
- Kalshi publishes resting bids on each side; a yes ask is implied by a no bid
  at `1 - price`. Both sides are stored as given.
- Option greeks and IV from either source are stored **as provided and treated
  as unverified**. All raw inputs needed to recompute IV are kept.
