"""The lab: try one step at a time, on a sample, before trusting it on everything.

    run.py lab inspect --config mvp --cycle 20260916T160000Z-...
    run.py lab score   --config mvp --vary vol.use=atm_first_expiry_after,strike_first_expiry_after
    run.py lab sweep   --config mvp --vary bar.min_ev=0.03,0.05,0.10

* **inspect** prints every market of one cycle after the pricing steps: its
  inputs, its vol, its fair, and the gate that ruled it out, if any. The way
  to see what a step actually does.
* **score** reprices the window once per value of one setting and scores each
  against outcomes (Brier, at each market's first priced cycle), beside the
  market's own mid. For the pricing steps (markets to gates): is a variant a
  better forecaster?
* **sweep** replays the window once per value of one setting and prints what
  each would have bet and earned against what it promised. For the decision
  steps (fee, bar, sizing, fills, account).

All three read whichever store `run.py --store` points at: a sample cut from
the real store (`run.py sample`) or the synthetic one runs in seconds.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import replay
import store
from pipeline import cache, config as configs, markets as markets_step
from pipeline.core import Cycle, NotReady
from pipeline.settle import payout_per_share, resolutions
from reporting import results


def window_cycles(dates: list[str]) -> list[str]:
    return [c for date in dates for c in store.session_cycles(date)]


def priced_window(config: dict, dates: list[str], use_cache: bool = True) -> pd.DataFrame:
    """Every priced market-cycle of the window, gated or not."""
    types = markets_step.contract_types(config["markets"])
    frames = []
    for cycle_id in window_cycles(dates):
        try:
            frame = cache.priced(Cycle(cycle_id, types), config, use_cache)
        except NotReady:
            continue
        if len(frame):
            frames.append(frame.assign(cycle_id=cycle_id))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def brier(config: dict, dates: list[str], settle_through: str | None = None,
          use_cache: bool = True):
    """Brier score of the config's fair against what happened, beside the mid.

    Scored once per market at its **first priced cycle** of the window: the
    cycles of a day are the same market repriced, and averaging them would
    count correlated observations as independent ones. Returns
    `(scores, sample)`; read `sample` first. A hit market that touches settles
    at once and one that does not waits for its deadline, so over a window
    shorter than the markets' horizons every settled market touched and the
    ranking says which estimate is highest, not which is right.
    """
    empty = pd.DataFrame(), {"markets": 0, "resolved": 0, "deadline_passed": 0,
                             "settled_early": 0, "base_rate": float("nan"),
                             "selection_biased": True}
    priced = priced_window(config, dates, use_cache)
    if priced.empty:
        return empty
    priced = priced[np.isfinite(priced["fair"].astype(float))]
    first = priced.sort_values("cycle_id").groupby("market_id", as_index=False).first()
    through = pd.Timestamp(settle_through or replay.latest_settlement())
    outcomes = resolutions(through)
    if outcomes.empty:
        return empty
    joined = first.merge(outcomes[["market_id", "result", "outcome_prices"]], on="market_id")
    joined["y"] = [payout_per_share(r, p, "YES")
                   for r, p in zip(joined["result"], joined["outcome_prices"])]
    joined = joined[joined["y"].notna()]
    if joined.empty:
        return empty
    at_deadline = (pd.to_datetime(joined["deadline"], utc=True) <= through).values
    passed, resolved = int(at_deadline.sum()), int(len(joined))
    # Only a market whose deadline has passed had the chance not to touch.
    if passed:
        joined = joined[at_deadline]
    base = float(joined["y"].mean())
    sample = {"markets": int(first["market_id"].nunique()), "resolved": resolved,
              "deadline_passed": passed, "settled_early": resolved - passed,
              "base_rate": base, "selection_biased": passed == 0 or base in (0.0, 1.0)}
    joined = joined.merge(market_mids(sorted(set(joined["cycle_id"])), config),
                          on=["cycle_id", "market_id"], how="left")
    rows = []
    for name in ("fair", "market_mid"):
        subset = joined[joined[name].notna()]
        if subset.empty:
            continue
        error = (subset[name].astype(float) - subset["y"].astype(float)) ** 2
        rows.append({"estimate": name, "n": int(len(subset)), "brier": float(error.mean()),
                     "mean_estimate": float(subset[name].mean()),
                     "base_rate": float(subset["y"].mean())})
    scored = pd.DataFrame(rows)
    if len(scored):
        scored["brier_vs_base_rate"] = scored["brier"] - base * (1 - base)
    return scored, sample


def market_mids(cycle_ids: list[str], config: dict) -> pd.DataFrame:
    """The YES mid at each cycle, as the market's own forecast."""
    types = markets_step.contract_types(config["markets"])
    rows = []
    for cycle_id in cycle_ids:
        book = store.books(cycle_id, types)
        top = book[(book["depth_rank"] == 0) & (book["outcome_index"] == 0)] if len(book) else book
        if top.empty:
            continue
        wide = top.pivot_table(index="market_id", columns="side", values="price", aggfunc="first")
        if "bid" not in wide or "ask" not in wide:
            continue
        mid = ((wide["bid"] + wide["ask"]) / 2.0).rename("market_mid").reset_index()
        rows.append(mid.assign(cycle_id=cycle_id, market_id=mid["market_id"].astype(str)))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["market_id", "market_mid", "cycle_id"])


# ------------------------------------------------------------------ commands


def _variants(base: str, vary: str | None, overrides: list[str]) -> list[tuple[str, dict]]:
    if not vary:
        return [(base, configs.load(base, overrides))]
    key, _, values = vary.partition("=")
    return [(f"{key}={v}", configs.load(base, overrides + [f"{key}={v}"]))
            for v in values.split(",")]


def inspect(config: dict, cycle_id: str) -> pd.DataFrame:
    types = markets_step.contract_types(config["markets"])
    return cache.priced(Cycle(cycle_id, types), config, use_cache=False)


def score(base: str, vary: str | None, overrides: list[str], dates: list[str]) -> pd.DataFrame:
    rows = []
    for label, config in _variants(base, vary, overrides):
        scored, sample = brier(config, dates)
        for row in scored.to_dict("records"):
            rows.append({"variant": label, **row, "deadline_passed": sample["deadline_passed"],
                         "selection_biased": sample["selection_biased"]})
    return pd.DataFrame(rows)


def sweep(base: str, vary: str | None, overrides: list[str], dates: list[str]) -> pd.DataFrame:
    rows = []
    for label, config in _variants(base, vary, overrides):
        m = results.summary(replay.run(config, dates))
        rows.append({"variant": label, **{k: m[k] for k in (
            "bets", "money_in", "promised_ev", "settled", "settled_promised_ev", "pnl",
            "realised_over_promised")}})
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="run.py lab", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["inspect", "score", "sweep"])
    parser.add_argument("--config", default="mvp")
    parser.add_argument("--vary", help="one setting and its values: step.key=v1,v2,...")
    parser.add_argument("--set", dest="overrides", action="append", default=[],
                        help="step.key=value, applied to every variant")
    parser.add_argument("--cycle", help="inspect: the cycle id")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    args = parser.parse_args(argv)
    dates = [d for d in store.dates() if (not args.start or d >= args.start)
             and (not args.end or d <= args.end)]
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    if args.command == "inspect":
        frame = inspect(configs.load(args.config, args.overrides), args.cycle)
        cols = [c for c in ("market_id", "ticker", "contract_type", "level", "spot", "sigma",
                            "T_years", "fair", "gate") if c in frame]
        print(frame[cols].to_string(index=False))
        return 0
    table = (score if args.command == "score" else sweep)(args.config, args.vary,
                                                          args.overrides, dates)
    print(table.to_string(index=False) if len(table) else "nothing to show")
    return 0

