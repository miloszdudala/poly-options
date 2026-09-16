# Prediction-market vs options data collection — market list

Goal of this stage: collect time-stamped snapshots only. No trading, no model fitting.
Everything below is read-only public data.

## 1. Polymarket markets

### Tier A — "close above" monthly ladders (pure digitals, primary)
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

### Tier B — "hit" monthly markets (one-touch barriers, secondary)
Titles of the form `What will <name> (<TICKER>) hit in September 2026?`:
ABNB, GOOGL, AMZN, AAPL, COIN, META, MU, MSFT, NVDA, NFLX, OPEN, PLTR, HOOD, RKLB, SPY, SPCX, TSLA, EWY, MSTR (near-zero liquidity).
Exclude SKHY (SK hynix; no US-listed options).

### Tier C — commodity / FX "hit" markets (collect Polymarket side only)
`What will Gold (XAUUSD) hit in September 2026?`, `What will Silver (XAGUSD) hit in September 2026?`,
`What will WTI Crude Oil (WTI) hit in September 2026?`, `What will US Dollar Index (DXY) hit in September 2026?`.
Parse and store the resolution source from the rules text; the options benchmark is decided later.

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
- Each run is a short script (fetch → append → exit) scheduled by launchd on macOS. Log every failed request.
- Retries with backoff and randomised pauses; yfinance is unofficial and throttles.