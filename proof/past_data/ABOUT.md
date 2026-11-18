# Past data, 16 September – 17 November 2026

The model replayed over the order books and option prices collected every 15
minutes from 16 September to 17 November 2026, on Polymarket's weekly "hit"
markets.

- **Pricing engine:** as of commit `4146d89` (17 November 2026).
- **Threshold:** a bet is taken when it is worth at least 3c a share more than
  it costs, fee included, and returns at least 8% (2% above a 90c price).
- **Size:** one share of every bet.

Files:

- `BETS.md`: every bet, with the 15-minute snapshot it was priced on and the
  best ask at that moment.
- `SETTLEMENTS.md`: how each market resolved on Polymarket.
- `PERFORMANCE.md`: one row per fully resolved week, built by
  `proof/performance.py` from the two files above.

Bets on the week of 20 November are listed without settlements. The live
record continues in `proof/` from 18 November.
