"""What would real-time option data have been worth?

Re-prices every session cycle twice with the current engine:
  base - the cycle's own chain (Cboe, 15 min delayed: what we actually had)
  rt   - the NEXT cycle's chain, whose prints date from about this cycle's
         book time: a stand-in for a real-time feed. Same spot, same bars,
         same touch check; only the option-implied vol changes.
The last cycle of each day has no next chain and is left out of both.
Then replays edge_taker / direction_balanced / scenario_hedged on each.
"""
import multiprocessing as mp
import os
import sys
from pathlib import Path

REPO = Path("/Users/miloszdudala/Code/poly-options")
OUT = Path(sys.argv[1])
sys.path.insert(0, str(REPO / "model"))
sys.path.append(str(REPO / "data" / "src"))


def work(task):
    cycle, nxt, mode = task
    import logging
    logging.disable(logging.WARNING)
    import data
    import fair_schema
    from engine import price_cycle
    if not getattr(data, "_retrying", False):
        import time as _t
        import duckdb as _d
        q = data._q
        def q_retry(sql):
            for i in range(6):
                try:
                    return q(sql)
                except _d.InvalidInputException:
                    if i == 5:
                        raise
                    _t.sleep(10)
        data._q = q_retry
        data._retrying = True
    orig = getattr(data, "_orig", None)
    if orig is None:
        data._orig = orig = (data.option_chain, data.quote_times, data.cboe_spot)
    if mode == "rt":
        data.option_chain = lambda c, symbols=None: orig[0](nxt, symbols)
        data.quote_times = lambda c: orig[1](nxt)
        data.cboe_spot = lambda c: orig[2](nxt)
    else:
        data.option_chain, data.quote_times, data.cboe_spot = orig
    frame, _ = price_cycle(cycle)
    if not len(frame):
        return cycle, mode, None
    frame["quote_age_true_s"] = frame["quote_age_s"]
    if mode == "rt":
        # The next chain is ~2 min newer than the book; the screen drops
        # negative ages as lookahead. Here that is the experiment, so clip.
        frame["quote_age_s"] = frame["quote_age_s"].clip(lower=0)
    fair_schema.STORE = OUT / mode
    fair_schema.write_fair(frame.to_dict("records"), data.cycle_date(cycle))
    return cycle, mode, float(frame["quote_age_true_s"].median())


if __name__ == "__main__":
    import data
    tasks = []
    for date in data.fair_dates():
        cycles = data.session_cycles(date)
        for c, n in zip(cycles, cycles[1:]):
            tasks += [(c, n, "base"), (c, n, "rt")]
    import pyarrow.parquet as pq
    done = set()
    for mode in ("base", "rt"):
        for f in (OUT / mode).rglob("*.parquet"):
            try:
                done |= {(c, mode) for c in pq.read_table(f, columns=["cycle_id"]).column(0).to_pylist()}
            except Exception:
                f.unlink()   # a part cut off when the pool died
    tasks = [t for t in tasks if (t[0], t[2]) not in done]
    print(len(tasks), "pricings left", flush=True)
    ages = {"base": [], "rt": []}
    with mp.get_context("spawn").Pool(7) as pool:
        for i, (c, mode, age) in enumerate(pool.imap_unordered(work, tasks)):
            if age is not None:
                ages[mode].append(age)
            if i % 50 == 0:
                print(i, c, mode, age, flush=True)
    import numpy as np
    for m, a in ages.items():
        print(m, "median quote age s", np.median(a))
