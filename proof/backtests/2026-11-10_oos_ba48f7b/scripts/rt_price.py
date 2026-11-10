"""Re-price each session cycle with the NEXT cycle's chain (real-time stand-in),
using ba48f7b's engine unmodified. Writes <wt>/model/store_rt/fair/date=*/.

Usage: python rt_price.py <worktree> <dates,...>
"""
import multiprocessing as mp
import sys
from pathlib import Path

WT = Path(sys.argv[1]).resolve()


def setup():
    sys.path.insert(0, str(WT / "model"))
    sys.path.append(str(WT / "data" / "src"))


def work(task):
    cycle, nxt = task
    setup()
    import logging
    logging.disable(logging.WARNING)
    import data
    import fair_schema
    from engine import price_cycle
    if not hasattr(data, "_orig"):
        data._orig = (data.option_chain, data.quote_times, data.cboe_spot)
    o = data._orig
    data.option_chain = lambda c, symbols=None: o[0](nxt, symbols)
    data.quote_times = lambda c: o[1](nxt)
    data.cboe_spot = lambda c: o[2](nxt)
    frame, _ = price_cycle(cycle)
    if not len(frame):
        return cycle, None
    age = float(frame["quote_age_s"].median())
    # The next chain can be up to ~2 min newer than the book; the screen would
    # drop negative ages as lookahead. Here that is the experiment, so clip.
    frame["quote_age_s"] = frame["quote_age_s"].clip(lower=0)
    fair_schema.STORE = WT / "model" / "store_rt"
    fair_schema.write_fair(frame.to_dict("records"), data.cycle_date(cycle))
    return cycle, age


if __name__ == "__main__":
    setup()
    import numpy as np
    import data
    tasks = []
    for date in sys.argv[2].split(","):
        cycles = data.session_cycles(date)
        tasks += list(zip(cycles, cycles[1:]))
    print(len(tasks), "cycles", flush=True)
    ages = []
    with mp.get_context("spawn").Pool(6) as pool:
        for i, (c, age) in enumerate(pool.imap_unordered(work, tasks)):
            if age is not None:
                ages.append(age)
            if i % 100 == 0:
                print(i, c, age, flush=True)
    print("done; median quote age s", np.median(ages), flush=True)
