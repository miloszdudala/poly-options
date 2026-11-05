"""The decision pipeline: how a bet is chosen, one step per module.

| # | step | module | MVP variant |
|---|---|---|---|
| 0 | venue: books, cycles, results | `venues.py` | `polymarket` |
| 1 | markets: which markets | `markets.py` | `polymarket_hit`, weekly, stocks |
| 2 | inputs: spot, clocks, chain age | `inputs.py` | `hit` |
| 3 | vol: which implied vol | `vol.py` | `atm_first_expiry_after` |
| 4 | prob: the probability YES wins | `prob.py` | `one_touch` |
| 5 | gates: which markets may be traded | `gates.py` | `standard` |
| 6 | fee: what a share costs | `offers.py` | `polymarket` |
| 7 | bar: which bets are worth it | `offers.py` | today's bar |
| 8 | sizing: which offers, how much (the model) | `sizing.py` | `edge_taker` |
| 9 | fills: what the book gives | `fills.py` | half a level, $1 minimum |
| 10 | account: how much may be spent | `accounts.py` | `max` |
| 11 | settle: what a resolved market pays | `settle.py` | - |

Steps 1-5 price a cycle (`core.price`, cached by `cache.py`); `replay.py` runs
6-11. Each step's module lists its variants and which of its choices are
decided, open or guessed; `DECISIONS.md` collects them. A variant is a
function (or class) registered with `@variant(step, name)`; a config picks it.
"""
from pipeline import (accounts, gates, inputs, markets, offers, prob, settle,  # noqa: F401
                      sizing, venues, vol, wti)
