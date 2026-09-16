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
| every 15 min, 09:30–16:00 ET on NYSE sessions | Polymarket + Kalshi books, option chains, spot |
| every hour otherwise | Polymarket + Kalshi books only |
| 17:30 ET, trading days | discovery, metadata, rates, dividends, earnings, bars, price history |

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

### Tables

`poly_markets`, `poly_books`, `poly_trades_last`, `poly_price_history`,
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
