# One-off experiments

Scripts behind results quoted in `INSIGHTS.md`, kept so they can be re-run
on more data. They are not part of the daily pipeline and patch `data.py` at
runtime rather than changing it.

| script | question | result |
|---|---|---|
| `realtime_options.py OUT` then `realtime_compare.py OUT` | would real-time option data raise profit? | no (INSIGHTS 7.6) |
| `small_book.py` | $50 books: best edges only, or weeklies bought Mon–Wed / Mon–Tue | 2026-10-17 session; 4 weeks, noise-level |

`realtime_options.py` re-prices every cycle twice (~800 engine runs, ~1 h on
7 cores). Each script waits and retries when it lands on a Parquet file the
collector is still writing.
