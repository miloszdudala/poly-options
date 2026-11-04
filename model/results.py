"""What a replay did, in numbers and in words: metrics, reports, ledgers.

`sim.py` runs the replay and returns a `sim.Result`; everything here reads one
and changes nothing. The Brier table over the whole priced universe
(`universe_scores`) lives here too, because it is a report and does not depend
on a model.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import data
import factors
import ledger_schema
import sim
from sim import NO, YES, Result

# ---------------------------------------------------------------- metrics


def fills_frame(result: Result) -> pd.DataFrame:
    rows = []
    for fill in result.fills:
        rows.append({"cycle_id": fill.cycle_id, "date": data.cycle_date(fill.cycle_id),
                     "market_id": fill.market_id, "outcome_index": fill.outcome_index,
                     "depth_rank": fill.depth_rank, "price": fill.price,
                     "shares": fill.shares, "fee_usd": fill.fee_usd,
                     "cost_usd": fill.cost_usd, "reason": fill.reason,
                     "level_size": fill.level_size})
    return pd.DataFrame(rows)


def settlements_frame(result: Result) -> pd.DataFrame:
    rows = []
    for s in result.settlements:
        p = s.position
        rows.append({
            "market_id": p.market_id, "outcome_index": p.outcome_index,
            "ticker": p.ticker, "contract_type": p.contract_type,
            "direction": p.direction, "quadrant": p.quadrant,
            "entry_cycle_id": p.entry_cycle_id, "entry_at": p.entry_at,
            "entry_fair": p.entry_fair, "entry_fair_engine": p.entry_fair_engine,
            "fair_source": p.fair_source, "win_prob": p.win_probability(p.entry_fair),
            "settled_at": s.settled_at, "cash_back_at": s.cash_back_at,
            "settle_date": pd.Timestamp(s.settled_at).date(),
            "result": s.result, "payout_per_share": s.payout_per_share,
            "won": s.payout_per_share >= 1.0,
            "shares": p.shares, "cost_usd": p.cost_usd, "fee_usd": p.fee_usd,
            "payout_usd": s.payout_usd, "pnl_usd": s.pnl_usd,
            "capital_days": s.capital_days})
    return pd.DataFrame(rows)


def open_frame(result: Result) -> pd.DataFrame:
    """The open book at the end, with the three marks MODELS 4.3 asks for."""
    if result.marks.empty:
        return pd.DataFrame()
    last = result.marks[result.marks["cycle_id"] == result.cycles[-1]]
    keys = {(p.market_id, p.outcome_index) for p in result.open_positions}
    rows = last[[(m, o) in keys for m, o in
                 zip(last["market_id"], last["outcome_index"])]]
    return rows.copy()


def metrics(result: Result) -> dict:
    """Every number the report prints, each with the count it came from.

    Deliberately flat: a nested structure reads well once and then has to be
    unpacked by every caller, and `run_all.py` wants to put four of these side
    by side in one table.
    """
    fills = fills_frame(result)
    settled = settlements_frame(result)
    capacity = result.capacity
    opened = open_frame(result)
    bankroll = result.params.bankroll
    out: dict = {  # noqa: E501
        "model": result.model, "run_id": result.run_id,
        "cycles": len(result.cycles), "days": len({data.cycle_date(c) for c in result.cycles}),
        "seconds": round(result.seconds, 1),
        "settled_through": result.settled_through,
        # Carried so a combined report can state the base its percentages are
        # shares of without reaching back into the Result.
        "params_bankroll": bankroll,
    }

    # -- capacity: wanted, allowed, filled, and what did the blocking -------
    out["orders"] = int(len(capacity))
    out["wanted_usd"] = float(capacity["wanted_usd"].sum()) if len(capacity) else 0.0
    out["budget_usd"] = float(capacity["budget_usd"].sum()) if len(capacity) else 0.0
    out["filled_usd"] = float(capacity["filled_usd"].sum()) if len(capacity) else 0.0
    out["fill_rate"] = (out["filled_usd"] / out["wanted_usd"]) if out["wanted_usd"] else float("nan")
    out["blockers"] = (capacity[capacity["blocker"] != "none"]["blocker"].value_counts().to_dict()
                       if len(capacity) else {})

    # -- fills -------------------------------------------------------------
    out["fills"] = int(len(fills))
    out["cost_usd"] = float(fills["cost_usd"].sum()) if len(fills) else 0.0
    out["fee_usd"] = float(fills["fee_usd"].sum()) if len(fills) else 0.0
    out["median_depth_rank"] = float(fills["depth_rank"].median()) if len(fills) else float("nan")

    # -- positions -----------------------------------------------------------
    out["positions_opened"] = int(len(settled) + len(result.open_positions))
    out["positions_resolved"] = int(len(settled))
    out["positions_open_at_end"] = int(len(result.open_positions))

    # -- resolved PnL: the headline, and it is resolved-only ------------------
    if len(settled):
        out["pnl_usd"] = float(settled["pnl_usd"].sum())
        out["resolved_cost_usd"] = float(settled["cost_usd"].sum())
        out["roi_resolved"] = (out["pnl_usd"] / out["resolved_cost_usd"]
                               if out["resolved_cost_usd"] else float("nan"))
        out["win_rate"] = float(settled["won"].mean())
        out["mean_entry_win_prob"] = float(settled["win_prob"].mean())
        out["capital_days"] = float(settled["capital_days"].sum())
        out["pnl_per_capital_day"] = (out["pnl_usd"] / out["capital_days"]
                                      if out["capital_days"] else float("nan"))
        # How much of the resolved sample is a touch. Over a window shorter
        # than the markets' horizons this is 1.0 for everybody, and a
        # resolved-only PnL then flatters YES and punishes NO by construction.
        out["resolved_yes_share"] = float((settled["result"] == "Yes").mean())
        out["resolved_on_no"] = int((settled["outcome_index"] == NO).sum())
    else:
        for key in ("pnl_usd", "resolved_cost_usd", "roi_resolved", "win_rate", "mean_entry_win_prob",
                    "capital_days", "pnl_per_capital_day", "resolved_yes_share"):
            out[key] = float("nan") if "rate" in key or "roi" in key or "prob" in key \
                or "share" in key else 0.0
        out["resolved_on_no"] = 0

    # -- the open book, three ways (MODELS 4.3) ------------------------------
    if len(opened):
        cost = float(opened["cost_usd"].sum())
        out["open_cost_usd"] = cost
        out["open_mark_fair_usd"] = float(np.nansum(opened["mark_fair_usd"]))
        out["open_mark_bid_usd"] = float(np.nansum(opened["mark_bid_usd"]))
        out["open_pnl_at_fair_usd"] = out["open_mark_fair_usd"] - cost
        out["open_pnl_at_bid_usd"] = out["open_mark_bid_usd"] - cost
    else:
        for key in ("open_cost_usd", "open_mark_fair_usd", "open_mark_bid_usd",
                    "open_pnl_at_fair_usd", "open_pnl_at_bid_usd"):
            out[key] = 0.0

    # -- exposure ------------------------------------------------------------
    equity = result.equity
    if len(equity):
        bdd = equity["beta_dollar_delta"].astype(float)
        out["bdd_median_usd"] = float(np.nanmedian(bdd))
        out["bdd_min_usd"] = float(np.nanmin(bdd))
        out["bdd_max_usd"] = float(np.nanmax(bdd))
        out["bdd_max_abs_frac"] = float(np.nanmax(np.abs(bdd)) / bankroll)
        out["equity_fair_end_usd"] = float(equity["equity_fair_usd"].iloc[-1])
        out["equity_bid_end_usd"] = float(equity["equity_bid_usd"].iloc[-1])
    else:
        for key in ("bdd_median_usd", "bdd_min_usd", "bdd_max_usd", "bdd_max_abs_frac"):
            out[key] = float("nan")
        out["equity_fair_end_usd"] = out["equity_bid_end_usd"] = bankroll

    # -- concentration -------------------------------------------------------
    everything = pd.concat([settled, _open_as_settled_shape(result)], ignore_index=True) \
        if len(settled) or result.open_positions else pd.DataFrame()
    if len(everything):
        by_ticker = everything.groupby("ticker")["cost_usd"].sum()
        total = float(everything["cost_usd"].sum())
        out["deployed_usd"] = total
        out["n_positions_concentration"] = int(len(everything))
        out["largest_ticker"] = str(by_ticker.idxmax())
        out["largest_ticker_share"] = float(by_ticker.max() / total) if total else float("nan")
        out["largest_position_share"] = float(everything["cost_usd"].max() / total) if total else float("nan")
        by_dir = everything.groupby("direction")["cost_usd"].sum()
        out["largest_direction"] = str(by_dir.idxmax())
        out["largest_direction_share"] = float(by_dir.max() / total) if total else float("nan")
        out["n_tickers"] = int(everything["ticker"].nunique())
        out["quadrants"] = everything.groupby("quadrant")["cost_usd"].sum().round(2).to_dict()
    else:
        out["largest_ticker"] = out["largest_direction"] = "-"
        out["deployed_usd"] = 0.0
        out["n_positions_concentration"] = 0
        out["largest_ticker_share"] = out["largest_position_share"] = float("nan")
        out["largest_direction_share"] = float("nan")
        out["n_tickers"] = 0
        out["quadrants"] = {}
    return out


def _open_as_settled_shape(result: Result) -> pd.DataFrame:
    rows = [{"ticker": p.ticker, "contract_type": p.contract_type,
             "direction": p.direction, "quadrant": p.quadrant,
             "cost_usd": p.cost_usd, "market_id": p.market_id,
             "outcome_index": p.outcome_index}
            for p in result.open_positions]
    return pd.DataFrame(rows)


def daily_pnl(result: Result) -> pd.DataFrame:
    """PnL by day, marked at fair, next to SPY's return that day.

    Two columns because they answer different questions. `pnl_marked` is the
    change in equity at the model's own fair across the day and is what
    "how did this model do on a -4% day" means; `pnl_settled` is cash that
    actually came back and lands entirely on the days things resolved.

    The index is the union of the days replayed and the days things settled.
    A weekly barrier bought on Wednesday settles on Friday, which is after the
    last cycle of a one-day window - leaving those days out would have the
    per-day settled PnL disagree with the headline, and it did.
    """
    equity = result.equity
    settled = settlements_frame(result)
    if equity.empty and settled.empty:
        return pd.DataFrame()
    last = equity.groupby("date")["equity_fair_usd"].last() if len(equity) else pd.Series(dtype=float)
    first_cycle = equity.groupby("date")["equity_fair_usd"].first() if len(equity) else pd.Series(dtype=float)
    booked = (settled.groupby(settled["settle_date"].astype(str))["pnl_usd"].sum()
              if len(settled) else pd.Series(dtype=float))
    booked_n = (settled.groupby(settled["settle_date"].astype(str))["pnl_usd"].size()
                if len(settled) else pd.Series(dtype=float))
    days = sorted(set(last.index) | set(booked.index))
    close = pd.Series([float(last.get(d, np.nan)) for d in days], index=days)
    start = close.shift(1)
    if len(start):
        start.iloc[0] = result.params.bankroll
    spy = factors.spy_daily_returns(days[0], days[-1])
    frame = pd.DataFrame({
        "equity_open_usd": start,
        "equity_close_usd": close,
        "equity_first_cycle_usd": [float(first_cycle.get(d, np.nan)) for d in days],
        "pnl_marked_usd": close - start,
        "pnl_settled_usd": [float(booked.get(d, 0.0)) for d in days],
        "n_settled": [int(booked_n.get(d, 0)) for d in days],
        "cycles": [int((equity["date"] == d).sum()) if len(equity) else 0 for d in days],
        "spy_return": [float(spy.get(pd.Timestamp(d).date(), float("nan"))) for d in days],
    })
    frame.index.name = "date"
    return frame


FAIR_BUCKETS = [0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.95, 1.0001]


def reliability(settled: pd.DataFrame, column: str = "win_prob") -> pd.DataFrame:
    """Did the positions win as often as they were supposed to?

    Bucketed on the position's own probability of winning at entry, so a NO
    bought at 5c sits in the 0.95+ bucket where it belongs rather than in the
    bucket of the touch it is betting against.
    """
    if settled.empty:
        return pd.DataFrame()
    frame = settled.dropna(subset=[column]).copy()
    if frame.empty:
        return pd.DataFrame()
    frame["bucket"] = pd.cut(frame[column], FAIR_BUCKETS, right=False)
    grouped = frame.groupby("bucket", observed=True)
    table = pd.DataFrame({
        "n": grouped.size(),
        "predicted": grouped[column].mean(),
        "realised": grouped["won"].mean(),
        "cost_usd": grouped["cost_usd"].sum(),
        "pnl_usd": grouped["pnl_usd"].sum(),
    })
    return table.reset_index()


def universe_scores(dates: list[str], settle_through: str | None = None):
    """Brier scores of every fair value against what actually happened.

    MODELS 4.5 calls this the scientific result, and it is the one number here
    that does not depend on a model, on sizing, or on whether anything filled.
    Scored once per market at its **first priced cycle** of the window: the 21
    cycles of a day are the same market repriced, and averaging them would
    report 21 correlated observations as 21 independent ones.

    Returns `(scores, sample)`. Read `sample` first. A hit market that touches
    settles the moment it touches; one that does not settles at its deadline.
    So over a window shorter than the markets' horizons, *every* market that
    has settled is one that touched, the base rate is 1.0, and the ranking
    below measures which estimate is highest rather than which is right. The
    `deadline_passed` count is how many markets could have gone either way.
    """
    frames = [data.fair_values(d) for d in dates]
    frames = [f for f in frames if len(f)]
    empty = pd.DataFrame(), {"markets": 0, "resolved": 0, "deadline_passed": 0,
                             "settled_early": 0, "base_rate": float("nan"),
                             "selection_biased": True}
    if not frames:
        return empty
    fair = pd.concat(frames, ignore_index=True).sort_values("cycle_id")
    first = fair.groupby("market_id", as_index=False).first()
    through = pd.Timestamp(settle_through or sim._latest_settlement())
    outcomes = data.resolutions_upto(through)
    if outcomes.empty:
        return empty
    joined = first.merge(outcomes[["market_id", "result", "outcome_prices"]],
                         on="market_id", how="inner")
    joined["y"] = [sim._payout_per_share(r, p, YES)
                   for r, p in zip(joined["result"], joined["outcome_prices"])]
    joined = joined[joined["y"].notna()]
    if joined.empty:
        return empty

    deadlines = pd.to_datetime(joined["deadline_utc"], utc=True, errors="coerce")
    at_deadline = (deadlines <= through).values
    passed = int(at_deadline.sum())
    resolved = int(len(joined))
    # Only a market whose deadline has passed had the chance not to touch. One
    # that settled early settled because it touched, and its untouched siblings
    # with the same deadline are still open - so mixing the two leans the base
    # rate toward YES. Measured 2026-09-23: once the 2026-09-18 weeklies passed
    # their deadline, the flag below turned off while touched monthlies still
    # sat in the sample. Score the deadline-passed markets only, as soon as
    # there are any.
    if passed:
        joined = joined[at_deadline]
    base = float(joined["y"].mean())
    sample = {"markets": int(first["market_id"].nunique()),
              "resolved": resolved, "deadline_passed": passed,
              "settled_early": resolved - passed,
              "base_rate": base,
              # Nothing has reached its deadline, so nothing in the sample had
              # the chance not to touch.
              "selection_biased": passed == 0 or base in (0.0, 1.0)}

    mids = _market_mids(sorted({data.cycle_date(c) for c in joined["cycle_id"]}))
    joined = joined.merge(mids, on=["cycle_id", "market_id"], how="left")

    rows = []
    for name in ("fair", "market_mid"):
        if name not in joined:
            continue
        subset = joined[joined[name].notna()]
        if subset.empty:
            continue
        error = (subset[name].astype(float) - subset["y"].astype(float)) ** 2
        rows.append({"estimate": name, "n": int(len(subset)),
                     "brier": float(error.mean()),
                     "mean_estimate": float(subset[name].mean()),
                     "base_rate": float(subset["y"].mean())})
    scored = pd.DataFrame(rows)
    if len(scored):
        scored["brier_vs_base_rate"] = scored["brier"] - base * (1 - base)
    return scored, sample


def _market_mids(dates: list[str]) -> pd.DataFrame:
    """The YES mid at each market's first priced cycle, as the market's own forecast."""
    rows = []
    for date in dates:
        for cycle_id in data.session_cycles(date):
            book = data.books(cycle_id)
            if book.empty:
                continue
            top = book[book["depth_rank"] == 0]
            yes = top[top["outcome_index"] == YES]
            wide = yes.pivot_table(index="market_id", columns="side", values="price",
                                   aggfunc="first")
            if "bid" not in wide or "ask" not in wide:
                continue
            mid = ((wide["bid"] + wide["ask"]) / 2.0).rename("market_mid").reset_index()
            mid["cycle_id"] = cycle_id
            rows.append(mid)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["market_id", "market_mid", "cycle_id"])


# ----------------------------------------------------------------- report


def _usd(x) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"${x:,.2f}"


def _pct(x) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.1%}"


def report_markdown(result: Result, m: dict | None = None) -> str:
    m = m or metrics(result)
    settled = settlements_frame(result)
    fills = fills_frame(result)
    daily = daily_pnl(result)
    start, end = result.window
    p = result.params
    lines = [
        f"# {result.model} — {start} to {end}",
        "",
        f"Run `{result.run_id}`, {m['cycles']} priced session cycles over "
        f"{m['days']} day(s), {m['seconds']}s. Outcomes read through "
        f"`{m['settled_through']}`.",
        "",
        f"Bankroll ${p.bankroll:,.0f}; per-market cap ${p.cap_yes_usd:,.0f} YES / "
        f"${p.cap_no_usd:,.0f} NO; per-ticker {p.ticker_cap_frac:.0%}; "
        f"per-direction {p.direction_cap_frac:.0%}; take {p.take_fraction:.0%} of a level; "
        f"fee {p.fee_rate:.0%} of min(p, 1-p).",
        "",
        "## Capacity",
        "",
        "| | dollars | count |",
        "|---|---:|---:|",
        f"| orders proposed | {_usd(m['wanted_usd'])} | {m['orders']:,} |",
        f"| allowed by the caps | {_usd(m['budget_usd'])} | |",
        f"| filled | {_usd(m['filled_usd'])} | {m['fills']:,} fills |",
        f"| fees paid | {_usd(m['fee_usd'])} | |",
        "",
        f"Fill rate {_pct(m['fill_rate'])} of what was wanted. "
        f"Median fill depth_rank {m['median_depth_rank']:.0f}.",
        "",
    ]
    if m["blockers"]:
        lines += ["What stopped the rest, by order count:", ""]
        lines += [f"- `{k}`: {v:,}" for k, v in sorted(m["blockers"].items(),
                                                       key=lambda kv: -kv[1])]
        lines.append("")

    lines += [
        "## Resolved PnL (the headline; MODELS 4.3 counts resolved only)",
        "",
        "| | |",
        "|---|---:|",
        f"| positions resolved | {m['positions_resolved']:,} |",
        f"| cost | {_usd(m['resolved_cost_usd'])} |",
        f"| PnL | {_usd(m['pnl_usd'])} |",
        f"| return on cost | {_pct(m['roi_resolved'])} |",
        "",
        f"Win rate {_pct(m['win_rate'])} on {m['positions_resolved']:,} resolved "
        f"positions against a mean entry probability of "
        f"{_pct(m['mean_entry_win_prob'])}. Capital-days "
        f"{m['capital_days']:,.1f}, PnL per capital-day "
        f"{_usd(m['pnl_per_capital_day'])}.",
        "",
        _selection_note(m),
        "",
        "## Open at the end, three ways (MODELS 4.3)",
        "",
        "| | dollars | count |",
        "|---|---:|---:|",
        f"| cost | {_usd(m['open_cost_usd'])} | {m['positions_open_at_end']:,} positions |",
        f"| marked at model fair | {_usd(m['open_mark_fair_usd'])} | "
        f"PnL {_usd(m['open_pnl_at_fair_usd'])} |",
        f"| marked at the last bid | {_usd(m['open_mark_bid_usd'])} | "
        f"PnL {_usd(m['open_pnl_at_bid_usd'])} |",
        f"| excluded | — | headline above |",
        "",
    ]

    rel = reliability(settled)
    lines += ["## Predicted against realised, positions only", ""]
    if len(rel):
        lines += ["| entry win prob | n | predicted | realised | cost | PnL |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in rel.itertuples():
            lines.append(f"| {row.bucket} | {row.n:,} | {row.predicted:.3f} | "
                         f"{row.realised:.3f} | {_usd(row.cost_usd)} | {_usd(row.pnl_usd)} |")
    else:
        lines.append("No resolved positions to score.")
    lines.append("")

    lines += ["## Exposure", "",
              f"Net beta-dollar-delta over {m['cycles']:,} cycles: median "
              f"{_usd(m['bdd_median_usd'])}, range {_usd(m['bdd_min_usd'])} to "
              f"{_usd(m['bdd_max_usd'])}; largest absolute is "
              f"{_pct(m['bdd_max_abs_frac'])} of bankroll.", ""]
    if len(daily):
        lines += ["| date | cycles | equity close (fair) | PnL marked | PnL settled | "
                  "n settled | SPY |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for date, row in daily.iterrows():
            spy = "-" if not np.isfinite(row["spy_return"]) else f"{row['spy_return']:+.2%}"
            lines.append(f"| {date} | {int(row['cycles']):,} | "
                         f"{_usd(row['equity_close_usd'])} | "
                         f"{_usd(row['pnl_marked_usd'])} | {_usd(row['pnl_settled_usd'])} | "
                         f"{int(row['n_settled']):,} | {spy} |")
        lines.append("")
        if len(daily) >= 3 and daily["spy_return"].notna().sum() >= 3:
            x = daily["spy_return"].astype(float).values
            y = daily["pnl_marked_usd"].astype(float).values
            ok = np.isfinite(x) & np.isfinite(y)
            slope = float(np.cov(x[ok], y[ok], bias=True)[0, 1] / np.var(x[ok]))
            lines += [f"PnL on SPY's daily return: slope {_usd(slope)} per 100% SPY move, "
                      f"{int(ok.sum())} days.", ""]
        else:
            lines += [f"PnL regressed on SPY's daily return needs three days; the window "
                      f"has {len(daily)}. Not computed.", ""]

    # Every share below is a share of this number, and it is printed because a
    # rate whose base the report never states is not a number anyone can check.
    base = (f"{_usd(m['deployed_usd'])} deployed across "
            f"{m['n_positions_concentration']:,} positions")
    lines += ["## Concentration", "",
              f"Shares of {base} (cost, resolved and open together).", "",
              f"- largest ticker `{m['largest_ticker']}` at {_pct(m['largest_ticker_share'])}, "
              f"across {m['n_tickers']:,} tickers",
              f"- largest single position {_pct(m['largest_position_share'])}",
              f"- largest direction `{m['largest_direction']}` at "
              f"{_pct(m['largest_direction_share'])}", ""]
    if m["quadrants"]:
        lines += ["| quadrant (MODELS 5.2b) | dollars |", "|---|---:|"]
        lines += [f"| {k} | {_usd(v)} |" for k, v in sorted(m["quadrants"].items())]
        lines.append("")

    if len(fills):
        by_reason = fills.groupby("reason").agg(
            fills=("cost_usd", "size"), cost_usd=("cost_usd", "sum")).reset_index()
        lines += ["## Fills by reason", "",
                  "| reason | fills | cost |", "|---|---:|---:|"]
        for row in by_reason.itertuples():
            lines.append(f"| {row.reason} | {row.fills:,} | {_usd(row.cost_usd)} |")
        lines.append("")
    return "\n".join(lines)


def _selection_note(m: dict) -> str:
    """Say out loud what a short window does to a resolved-only PnL.

    A hit market settles the moment it touches; one that does not touch waits
    for its deadline. So over a window shorter than the markets' horizons the
    only settled markets are the touched ones, every resolved position on the
    NO side is a loss and every one on the YES side is a win, and the headline
    PnL is a count of which side the model happened to be on. This is not a
    caveat to be read later - it is the first thing to know about the number
    above it.
    """
    resolved = m.get("positions_resolved", 0)
    if not resolved:
        return "No position has resolved yet, so there is no headline to read."
    share = m.get("resolved_yes_share", float("nan"))
    if not (np.isfinite(share) and share >= 0.999):
        return (f"{share:.0%} of the {resolved:,} resolved markets resolved Yes.")
    return (f"**All {resolved:,} of those markets resolved Yes**, and "
            f"{m.get('resolved_on_no', 0):,} of the positions were on the NO "
            f"side. That is selection, not skill or its absence: a hit market "
            f"settles the instant it touches and otherwise waits for its "
            f"deadline, so a window shorter than the markets' horizons resolves "
            f"nothing but touches. This PnL measures which side of a touched "
            f"market the model was on. It becomes a result when the deadlines "
            f"pass and the markets that did not touch settle too.")


REPORTS = ledger_schema.STORE.parent / "reports"


def write_run(result: Result, m: dict | None = None) -> dict:
    """Ledger to Parquet, report to Markdown. Returns the paths written."""
    m = m or metrics(result)
    start, end = result.window
    fills = [{"run_id": result.run_id, "model": result.model, "cycle_id": f.cycle_id,
              "filled_at_utc": f.filled_at.isoformat(), "market_id": f.market_id,
              "outcome_index": f.outcome_index, "depth_rank": f.depth_rank,
              "price": f.price, "shares": f.shares, "fee_usd": f.fee_usd,
              "cost_usd": f.cost_usd, "limit_price": f.limit_price, "fair": f.fair,
              "fair_source": f.fair_source, "reason": f.reason,
              "level_size": f.level_size,
              **_position_meta(result, f.market_id, f.outcome_index)}
             for f in result.fills]
    settlements = []
    for s in result.settlements:
        p = s.position
        settlements.append({
            "run_id": result.run_id, "model": result.model, "market_id": p.market_id,
            "outcome_index": p.outcome_index, "ticker": p.ticker,
            "contract_type": p.contract_type, "barrier": p.barrier,
            "entry_cycle_id": p.entry_cycle_id, "entry_at_utc": p.entry_at.isoformat(),
            "settled_at_utc": s.settled_at.isoformat(),
            "cash_back_at_utc": s.cash_back_at.isoformat(), "result": s.result,
            "payout_per_share": s.payout_per_share, "shares": p.shares,
            "cost_usd": p.cost_usd, "fee_usd": p.fee_usd, "payout_usd": s.payout_usd,
            "pnl_usd": s.pnl_usd, "entry_fair": p.entry_fair,
            "entry_fair_engine": p.entry_fair_engine, "fair_source": p.fair_source,
            "capital_days": s.capital_days})
    paths = {
        "fills": ledger_schema.write_ledger(fills, "fills", result.model, result.run_id),
        "marks": ledger_schema.write_ledger(result.marks.to_dict("records"), "marks",
                                            result.model, result.run_id),
        "resolutions": ledger_schema.write_ledger(settlements, "resolutions",
                                                  result.model, result.run_id),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"{result.model}_{start}_{end}.md"
    report_path.write_text(report_markdown(result, m))
    paths["report"] = report_path
    return paths


def _position_meta(result: Result, market_id: str, outcome_index: int) -> dict:
    for pos in list(result.open_positions) + [s.position for s in result.settlements]:
        if pos.market_id == market_id and pos.outcome_index == outcome_index:
            return {"ticker": pos.ticker, "contract_type": pos.contract_type,
                    "barrier": pos.barrier,
                    "deadline_utc": None if pd.isna(pos.deadline) else str(pos.deadline),
                    "period": pos.period}
    return {"ticker": None, "contract_type": None, "barrier": None,
            "deadline_utc": None, "period": None}
