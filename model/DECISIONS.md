# Decisions: every choice the pipeline makes, and how sure we are of it

One row per choice, in pipeline order. **Status**: *decided* (in `CLAUDE.md`
or measured and agreed), *open* (the user's to decide), *guess* (a number or
rule picked to make the code run, with little or no measurement behind it:
the places to improve first). Every choice is a setting in
`model/configs/mvp.yaml` or a variant in its step's module, so each can be
changed on its own and tried with the lab before it is trusted:

```bash
.venv/bin/python model/run.py sample --from 2026-10-05 --to 2026-10-09 --tickers NVDA,PLTR,TSLA --out model/samples/w1
.venv/bin/python model/run.py --store model/samples/w1 lab score --vary vol.use=atm_first_expiry_after,strike_first_expiry_after
.venv/bin/python model/run.py --store model/samples/w1 lab sweep --vary bar.min_ev=0.03,0.05,0.10
.venv/bin/python model/run.py --store model/samples/w1 lab inspect --cycle <cycle_id>
```

`score` is for the pricing steps (1-5: is it a better forecaster?), `sweep`
for the decision steps (6-10: what would it have bet and earned?), `inspect`
for seeing what a step did to every market of one cycle.

| step | choice | setting | status | evidence / note | try it |
|---|---|---|---|---|---|
| 1 markets | Polymarket weekly hit markets on stocks first | `markets.period`, `use` | decided (MVP scope) | other kinds are extensions in `configs/extensions/` | `--config hit_monthly` |
| 1 markets | commodities, FX and their ETFs left out | `markets.exclude_tickers` | decided | WTI has its own extension (`wti_monthly`); gold, silver, DXY Open | - |
| 1 markets | deadline = last NYSE close at or before `end_date` | `markets.py` | decided | a barrier only prints in regular hours | - |
| 2 inputs | spot = open of the 5-minute bar containing the fetch | `inputs.hit` | decided | review 2026-09-18: the close is lookahead | - |
| 2 inputs | no bar: Cboe's 15-minute-delayed last | `inputs.hit` | guess | rare; flagged `spot_source` | `lab inspect` |
| 2 inputs | refuse a cycle whose 5-minute bars have not arrived | `inputs.require_bars` | decided | review 2026-10-09: 40 fills on printed barriers | - |
| 2 inputs | time = NYSE session time to the deadline | `inputs.hit` | decided | weekends carry no monitoring | - |
| 3 vol | ATM vol of the first expiry at or after the deadline | `vol.use` | decided | Brier 0.0912 vs barrier-strike 0.0922 (7,750 resolved, 2026-11-03) | `lab score --vary vol.use=...` |
| 3 vol | chain older than 30 minutes = no chain | `vol.stale_chain_s` | guess | first 15-30 min of a session carry yesterday's prints (2026-09-18) | `lab score --vary vol.stale_chain_s=900,1800,3600` |
| 3 vol | contracts with OI >= 100 or volume > 0, bid > 0 | `store.option_chain` | decided | FINDINGS 4: untraded IVs disagree by 59 points at p90 | - |
| 3 vol | IV recomputed from the mid, OTM only, flat outside the liquid strikes | `maths.build_smile` | guess (flat = conservative) | FINDINGS 5.2 | - |
| 3 vol | an earnings date between deadline and expiry: not traded (no repair) | `gates.drop: earnings_gap` | guess | follows from the ATM rule; a pre-earnings repair existed before T2 | `lab score --set "gates.drop=[touch_pending, no_fair, deep_otm]"` |
| 4 prob | touch over regular hours only (overnight gap unwatched, session watched) | `prob.use: regular_hours` | decided (2026-11-05) | Brier 0.0788 vs continuous 0.0799 on 9,272 finished-market observations | `lab score --vary prob.use=regular_hours,one_touch` |
| 4 prob | session share of variance `f` from the stock's daily bars at the cycle | `prob.min_sessions` (60), `prob.f_clip` | decided (CLAUDE.md: the one input not from options) | 0.52-0.75 for most stocks | `lab score --vary prob.min_sessions=40,60,120` |
| 4 prob | `f` = 0.6 with under 60 sessions of bars | `prob.default_f` | guess | INSIGHTS 3.3 expected 0.5-0.7 | `lab score --vary prob.default_f=0.5,0.6,0.7` |
| 4 prob | 20,000 antithetic paths, fixed seed | `prob.paths`, `prob.seed` | guess | Monte Carlo error ~0.3c | `lab score --vary prob.paths=20000,80000` |
| 4 prob | rate = 1-month T-bill; no dividends | `store.risk_free_rate` | guess | small over days to a month | - |
| 5 gates | a touch already printed: fair 1, not traded | `gates.drop: touch_pending` | decided | review 2026-09-18 | - |
| 5 gates | barrier over 2.5 ATM standard moves away: not traded | `gates.deep_otm_sd` | guess | FINDINGS 5.2 (fair 0.002-0.147 on one barrier) | `lab score --vary gates.deep_otm_sd=2,2.5,3` |
| 5 gates | chain prints newer than the book: not traded | `gates.drop: quote_ahead_of_book` | decided | review 2026-09-16 | - |
| 6 fee | the market's own schedule at the time: `rate x (p(1-p))^exponent`; 0.04 if never recorded | `fee.use: polymarket` | decided ("true venue fees") | most markets went to zero fees 2026-10-20 to 10-30 | - |
| 7 bar | EV >= 3c and return >= 8% | `bar.min_ev`, `bar.min_roi` | open | | `lab sweep --vary bar.min_ev=...` |
| 7 bar | above 90c: return >= 2% instead | `bar.dear_*` | open | 3c on a 95c NO is never on offer | `lab sweep --vary bar.dear_min_roi=...` |
| 7 bar | YES charged a 2c risk premium | `bar.yes_premium` | decided (2026-10-09) | INSIGHTS 7.2 | - |
| 8 sizing | take every offer, as large as the book allows | `sizing.use: edge_taker` | decided (MVP) | the hedged books are the user's to write | - |
| 8 sizing | offers best return first | `offers.make_offers` | guess | matters only when cash is limited | - |
| 8 sizing | one market, one side, once (no averaging down) | `replay.py` | decided | MODELS 4.2 | - |
| 8 sizing | the control: 12 random bets a cycle, 2c slip | `configs/control.yaml` | guess | a yardstick, not a strategy | - |
| 9 fills | at most half of each level | `fills.take_fraction` | guess | the rest may be gone when the order lands | `lab sweep --vary fills.take_fraction=0.25,0.5,1` |
| 9 fills | a walk under $1 is no fill | `fills.min_fill_usd` | decided | Polymarket's minimum order | - |
| 9 fills | a fill counts if the book had the size when we looked | `fills.py` | decided | the ~40 s recheck: 80-88% still buyable | - |
| 10 account | max: no ceiling | `account.use` | decided | | `--set "account={use: compound, start: 100}"` |
| 11 settle | 50-50 pays 0.5; a pending dispute is not booked | `settle.py` | decided | venue rules | - |

## Extensions (kept, not developed: CLAUDE.md Focus)

| config | the choices that differ from the MVP | status |
|---|---|---|
| `hit_monthly` | `markets.period: monthly` | as the MVP |
| `close_above_*`, `range_weekly`, `up_down_daily` | `prob: call_spread` (out-of-the-money side; close-settled options only; expiry must equal the deadline); up/down strike = the previous close (98.7% of 760 resolved), "Opens" markets ruled out | lose money or not believable yet (CLAUDE.md) |
| `kalshi_sp500_daily`, `kalshi_ndx_daily` | `venue: kalshi`, `markets: kalshi_index`, `fee: kalshi` 0.07 x p(1-p) (Kalshi API, read 2026-11-04); levels read to the cent | books hourly; option prices 15 minutes delayed |
| `wti_monthly` | spot on the contract Active at the deadline, USO vol at the same moneyness x `vol.vol_ratio`, continuous one-touch, touches in ICE hours | prices nothing until `vol_ratio` is set (open: 1.06-1.09) and next-contract prices are collected |
