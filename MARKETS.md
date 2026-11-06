# Prediction-market vs options data collection — market list

The original collection spec (September 2026), kept as written except where
marked. Everything below is read-only public data. Since then the hit markets
became the main product, and `model/` prices and simulates every market kind
listed here (`model/MODELS.md`, types).

## 1. Polymarket markets

### Tier A — "close above" monthly ladders (pure digitals; primary in the original spec, now ~19¢ wide, README)
Known event slug pattern: `{ticker}-above-in-{month}-{year}` (e.g. `tsla-above-in-september-2026`).
Verified for September 2026: 13 strikes each.

| Ticker | Title |
|---|---|
| AAPL | Will Apple (AAPL) close above ___ end of September? |
| MSFT | Will Microsoft (MSFT) close above ___ end of September? |
| NVDA | Will NVIDIA (NVDA) close above ___ end of September? |
| AMZN | Will Amazon (AMZN) close above ___ end of September? |
| GOOGL | Will Google (GOOGL) close above ___ end of September? |
| META | Will Meta (META) close above ___ end of September? |
| NFLX | Will Netflix (NFLX) close above ___ end of September? |
| TSLA | Will Tesla (TSLA) close above ___ end of September? |
| PLTR | Will Palantir (PLTR) close above ___ end of September? |
| MU | Will Micron (MU) close above ___ end of September? |
| OPEN | Will Opendoor (OPEN) close above ___ end of September? (secondary) |
| SPCX | Will SpaceX (SPCX) close above ___ end of September? (secondary; options may not exist) |

Discovery must also run for future months (October ladders are not listed yet) and must not rely only on the slug pattern: search all open events whose title matches `close above ___ end of`.

### Tier B — "hit" monthly markets (one-touch barriers; secondary in the original spec, now the main product)
Titles of the form `What will <name> (<TICKER>) hit in September 2026?`:
ABNB, GOOGL, AMZN, AAPL, COIN, META, MU, MSFT, NVDA, NFLX, OPEN, PLTR, HOOD, RKLB, SPY, SPCX, TSLA, EWY, MSTR (near-zero liquidity).
Exclude SKHY (SK hynix; no US-listed options).

### Tier C — commodity / FX "hit" markets (collect Polymarket side only)
`What will Gold (XAUUSD) hit in September 2026?`, `What will Silver (XAGUSD) hit in September 2026?`,
`What will WTI Crude Oil (WTI) hit in September 2026?`, `What will US Dollar Index (DXY) hit in September 2026?`.
Parse and store the resolution source from the rules text; the options benchmark is decided later.
*(Since decided for WTI: USO options, `CLAUDE.md`. Gold, silver and the dollar index are Open.)*

### Weekly / daily series (discovery required)
Earlier scans showed weekly events such as `What will S&P 500 (SPY) hit Week of September 14 2026?`,
`Will SpaceX (SPCX) finish week of September 14 above___?`, `S&P 500 (SPY) closes above ___ on September 14?`,
and daily `Up or Down` markets. A date-window scan of the Gamma API missed them.
Use `/events/keyset` pagination (the offset endpoint rejects offsets above ~2000) over all open events and keep any
event whose title contains an equity/ETF ticker in parentheses together with one of:
`close above`, `closes above`, `finish week`, `closes week`, `hit`, `Up or Down`.

### Exclude
Earnings "beat" markets, GPU rental prices, macro, IPO, valuation, esports/sports (false ticker matches such as `(BO3)`).

## 2. Kalshi series (paper track; collect all open events in each series)

Exact-match digitals and ranges on the index close:
`KXINXU`, `KXINX`, `KXINXDUD`, `KXNASDAQ100U`, `KXNASDAQ100`, `KXNASDAQDUD`.
Year-end: `KXINXY`, `KXINXDIRY`, `KXNASDAQ100Y`, `KXNDQDIRY`, `KXINXPOS`, `KXNASDAQ100POS`.
Barriers (secondary): `KXINXMAXY`, `KXINXMINY`, `KXNASDAQ100MAXY`, `KXNASDAQ100MINY`, `KXSP500WHEN`.
Base URL: `https://api.elections.kalshi.com/trade-api/v2` (public market data).

## 3. Options chains to collect

Equities/ETFs: AAPL, MSFT, NVDA, AMZN, GOOGL, META, NFLX, TSLA, PLTR, MU, OPEN, SPCX (skip if no chain), COIN, HOOD, RKLB, ABNB, SPY, EWY, MSTR.
Indices: SPX, NDX (for Kalshi).
Commodity/FX proxies (tier C, low priority): GLD, SLV, USO, UUP.

For each underlying, store every expiry up to 45 days out plus the December expiry (for year-end Kalshi markets).
Per option: timestamp of fetch, quote timestamp if provided, expiry, strike, type, bid, ask, last, volume, open interest.
Also store the underlying spot with its timestamp.

## 4. Snapshot requirements

- Polymarket: for every market, the order book (best bid/ask and depth levels) from the CLOB API, plus the market's
  token IDs, strike parsed from the question, end date, and the full rules text (stored once per market).
- Kalshi: yes bid/ask, depth if available, volume, open interest, strike/range parsed from the market.
- Store every record with a UTC fetch timestamp. Polymarket snapshots must be kept at every cycle so they can later be
  aligned with the ~15-minute delayed options quotes.
- Schedule: every 15 minutes during US regular hours (09:30–16:00 America/New_York; use the tz database, do not
  hard-code Singapore offsets). Polymarket and Kalshi may additionally be sampled hourly outside those hours.
- Storage: SQLite, one table per venue plus an options table and a market-metadata table.
  *(Not what was built: the store is Parquet under `data/store/parquet/`, one directory per table, README.)*
- Each run is a short script (fetch → append → exit) scheduled by launchd on macOS. Log every failed request.
- Retries with backoff and randomised pauses; yfinance is unofficial and throttles.

## Measured liquidity history (checked 2026-09-16)

Cumulative lifetime volume per event, fetched from Gamma by slug, for the same
ten equity tickers (AAPL MSFT NVDA AMZN GOOGL META NFLX TSLA PLTR MU). This was
run to test whether the "hit" markets absorbed the volume that drained out of
"close above". **They did not — every series shrank.**

| month | monthly close-above | weekly close-above | hit |
|---|---|---|---|
| Feb | 1,914,381 | 3,711,621 | 2,439,692 |
| Mar | 1,648,466 | 609,982 | 2,470,340 |
| Apr | 361,984 | 865,671 | 864,164 |
| May | *not listed* | 503,188 | 2,477,521 |
| Jun | 158,607 | 507,934 | 943,835 |
| Jul | 105,149 | 272,761 | 886,835 |
| Aug | 70,173 | 217,062 | 538,803 |
| Sep (to 16th) | 2,492 | 148,345 | 257,244 |

Feb to Aug: monthly close-above -96%, weekly close-above -94%, hit -78%. The
whole equity category fell from ~8.1M to ~0.8M a month. Hit's *share* of the two
rose from 56% to 88%, which looks like rotation until you notice it is a bigger
slice of a much smaller pie.

So the thing to watch for before trusting these markets again is a
**category-wide** recovery, not flow rotating back into close-above
*(unapproved)*. Monthly
close-above ladders were simply not listed for May 2026, which is worth knowing
before reading a zero as a collapse.

Caveats: September is a partial month (16 of 30 days). Volume is the venue's own
cumulative figure per event, not reconstructed from trades. Commodity and FX hit
markets are excluded here - WTI alone did 4.0M in September, an order of
magnitude more than every tracked equity market combined, and mixing it in hides
the equity trend entirely.
