"""What a replay did, in numbers and in words: summaries, reports, ledgers.

`sim.py` runs a replay and returns a `sim.Result`; everything here reads one
and changes nothing. Every report sets the **EV promised at entry** against
the **PnL realised**, per model x type. The Brier table over the whole priced
universe (`universe_scores`) is here too: it is a report about the hit-market
fair and does not depend on a model.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import data
import ledger_schema
import sim
from models import YES

REPORTS = ledger_schema.STORE.parent / "reports"
PERIOD_NOUN = {"daily": "day", "weekly": "week", "monthly": "month"}


def latest_settlement() -> str:
    """The newest Polymarket settlement on disk."""
    frame = data.resolutions_upto("2100-01-01")
    if frame.empty:
        return "2100-01-01"
    return str(pd.Timestamp(frame["settled_at_utc"].max()))


# ------------------------------------------------------------------ frames


def bets_frame(result: sim.Result) -> pd.DataFrame:
    """One row per bet, settled or open."""
    rows = [{
        "model": result.model, "type": result.type, "account": result.account,
        "market_id": p.market_id, "side": p.side,
        "outcome_index": sim.SIDE_INDEX[p.side], "ticker": p.ticker,
        "contract_type": p.contract_type, "level": p.level,
        "deadline_utc": None if p.deadline is None or pd.isna(p.deadline) else str(p.deadline),
        "period": p.period, "direction": p.direction, "vol": p.vol,
        "entry_cycle_id": p.entry_cycle_id, "entry_at": p.entry_at,
        "entry_at_utc": p.entry_at.isoformat(),
        "entry_period": sim.period_key(p.entry_at, result.period),
        "win_prob": p.win_prob, "shares": p.shares, "cost_usd": p.cost_usd,
        "fee_usd": p.fee_usd, "ev_usd": p.ev_usd, "settled": p.settled,
        "result": p.result, "payout_usd": p.payout_usd, "pnl_usd": p.pnl_usd,
        "won": None if p.payout_usd is None else p.payout_usd >= p.shares - 1e-9,
        "settled_at_utc": None if p.settled_at is None else p.settled_at.isoformat(),
    } for p in result.positions]
    return pd.DataFrame(rows)


def fills_frame(result: sim.Result) -> pd.DataFrame:
    rows = []
    for p in result.positions:
        for f in p.fills:
            rows.append({
                "model": result.model, "type": result.type, "account": result.account,
                "market_id": p.market_id, "side": p.side,
                "outcome_index": sim.SIDE_INDEX[p.side], "ticker": p.ticker,
                "contract_type": p.contract_type, "level": p.level,
                "deadline_utc": None if pd.isna(p.deadline) else str(p.deadline),
                "period": p.period, "cycle_id": f.cycle_id, "date": data.cycle_date(f.cycle_id),
                "filled_at_utc": f.filled_at.isoformat(), "depth_rank": f.depth_rank,
                "price": f.price, "shares": f.shares, "fee_usd": f.fee_usd,
                "cost_usd": f.cost_usd, "ev_usd": f.ev_usd, "win_prob": p.win_prob,
                "level_size": f.level_size})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- numbers


def summary(result: sim.Result) -> dict:
    """The headline of one model x type x account: what went in, what was
    promised at entry, and what came back."""
    bets = bets_frame(result)
    out = {"model": result.model, "type": result.type, "account": result.account,
           "cycles": len(result.cycles), "settled_through": result.settled_through,
           "seconds": round(result.seconds, 1), "skipped": dict(result.skipped),
           "bets": int(len(bets))}
    if bets.empty:
        return out | {"money_in": 0.0, "fees": 0.0, "promised_ev": 0.0, "settled": 0,
                      "settled_cost": 0.0, "settled_promised_ev": 0.0, "pnl": 0.0,
                      "realised_over_promised": float("nan"), "win_rate": float("nan"),
                      "mean_win_prob": float("nan"), "open": 0, "open_cost": 0.0,
                      "periods_settled": 0, "resolved_yes_share": float("nan")}
    done = bets[bets["settled"]]
    promised = float(done["ev_usd"].sum())
    pnl = float(done["pnl_usd"].sum()) if len(done) else 0.0
    open_ = bets[~bets["settled"]]
    # A period counts as settled once every bet entered in it has resolved.
    by_period = bets.groupby("entry_period")["settled"].all()
    return out | {
        "money_in": float(bets["cost_usd"].sum()),
        "fees": float(bets["fee_usd"].sum()),
        "promised_ev": float(bets["ev_usd"].sum()),
        "settled": int(len(done)),
        "settled_cost": float(done["cost_usd"].sum()),
        "settled_promised_ev": promised,
        "pnl": pnl,
        "realised_over_promised": pnl / promised if promised else float("nan"),
        "win_rate": float(done["won"].astype(float).mean()) if len(done) else float("nan"),
        "mean_win_prob": float(done["win_prob"].mean()) if len(done) else float("nan"),
        "open": int(len(open_)), "open_cost": float(open_["cost_usd"].sum()),
        "periods_settled": int(by_period.sum()),
        "resolved_yes_share": (float((done["result"] == "Yes").mean())
                               if len(done) else float("nan")),
    }


def by_period(result: sim.Result) -> pd.DataFrame:
    """Bets grouped by the period they were entered in (the type's own: week
    from Monday, or month), with the EV promised beside the PnL realised."""
    bets = bets_frame(result)
    if bets.empty:
        return pd.DataFrame()
    bets["settled_ev"] = np.where(bets["settled"], bets["ev_usd"], 0.0)
    bets["pnl"] = bets["pnl_usd"].fillna(0.0)
    out = bets.groupby("entry_period").agg(
        bets=("market_id", "size"), money_in=("cost_usd", "sum"),
        promised_ev=("ev_usd", "sum"), settled=("settled", "sum"),
        settled_promised_ev=("settled_ev", "sum"), pnl=("pnl", "sum"))
    out.index.name = "period"
    return out


FAIR_BUCKETS = [0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.95, 1.0001]


def reliability(bets: pd.DataFrame, column: str = "win_prob") -> pd.DataFrame:
    """Did the settled bets win as often as they were supposed to?

    Bucketed on the bet's own probability of winning at entry, so a NO bought
    at 5c sits in the 0.95+ bucket where it belongs.
    """
    if bets.empty:
        return pd.DataFrame()
    frame = bets[bets["settled"]].dropna(subset=[column]).copy()
    if frame.empty:
        return pd.DataFrame()
    frame["won"] = frame["won"].astype(float)
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
    through = pd.Timestamp(settle_through or latest_settlement())
    outcomes = data.resolutions_upto(through)
    if outcomes.empty:
        return empty
    joined = first.merge(outcomes[["market_id", "result", "outcome_prices"]],
                         on="market_id", how="inner")
    joined["y"] = [sim.payout_per_share(r, p, YES)
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
            yes = top[top["outcome_index"] == sim.SIDE_INDEX[YES]]
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


SUMMARY_COLUMNS = [
    ("model", "model", "{}"),
    ("type", "type", "{}"),
    ("bets", "bets", "{:,}"),
    ("money in", "money_in", "${:,.0f}"),
    ("promised EV", "promised_ev", "${:,.2f}"),
    ("settled", "settled", "{:,}"),
    ("promised (settled)", "settled_promised_ev", "${:,.2f}"),
    ("PnL (settled)", "pnl", "${:,.2f}"),
    ("realised / promised", "realised_over_promised", "{:.2f}"),
    ("periods settled", "periods_settled", "{:,}"),
]


def summary_table(rows: list[dict]) -> str:
    """One row per model x type. Promised EV and PnL are compared on the same
    (settled) bets; `promised EV` on its own includes the open ones."""
    header = "| " + " | ".join(h for h, _, _ in SUMMARY_COLUMNS) + " |"
    rule = "|" + "|".join(["---", "---"] + ["---:"] * (len(SUMMARY_COLUMNS) - 2)) + "|"
    lines = [header, rule]
    for row in rows:
        cells = []
        for _, key, fmt in SUMMARY_COLUMNS:
            value = row.get(key)
            if value is None or (isinstance(value, float) and not np.isfinite(value)):
                cells.append("-")
            else:
                cells.append(fmt.format(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def period_table(result: sim.Result) -> list[str]:
    table = by_period(result)
    if table.empty:
        return ["No bets."]
    lines = [f"| {PERIOD_NOUN.get(result.period, result.period)} | bets | money in | promised EV | settled | "
             "promised (settled) | PnL (settled) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for period, row in table.iterrows():
        lines.append(f"| {period} | {int(row['bets']):,} | {_usd(row['money_in'])} | "
                     f"{_usd(row['promised_ev'])} | {int(row['settled']):,} | "
                     f"{_usd(row['settled_promised_ev'])} | {_usd(row['pnl'])} |")
    return lines


def report_markdown(result: sim.Result, m: dict | None = None) -> str:
    m = m or summary(result)
    cycles = result.cycles
    start = data.cycle_date(cycles[0]) if cycles else "-"
    end = data.cycle_date(cycles[-1]) if cycles else "-"
    lines = [
        f"# {result.model} on {result.type} ({result.account}) - {start} to {end}",
        "",
        f"{m['cycles']:,} cycles, {m['seconds']}s. Outcomes read through "
        f"`{m['settled_through']}`.",
        "",
        "## Promised against realised",
        "",
        summary_table([m]),
        "",
        f"Win rate {_pct(m.get('win_rate'))} on {m['settled']:,} settled bets against a "
        f"mean entry probability of {_pct(m.get('mean_win_prob'))}. "
        f"{m['open']:,} bets ({_usd(m['open_cost'])}) are still open. "
        f"Fees paid {_usd(m['fees'])}.",
        "",
        _selection_note({"positions_resolved": m["settled"],
                         "resolved_yes_share": m.get("resolved_yes_share")}),
        "",
        f"## By {PERIOD_NOUN.get(result.period, result.period)} of entry",
        "",
        *period_table(result),
        "",
    ]
    if m["skipped"]:
        lines += ["## Markets skipped", ""]
        lines += [f"- {reason}: {n:,}" for reason, n in sorted(m["skipped"].items())]
        lines.append("")
    rel = reliability(bets_frame(result))
    lines += ["## Predicted against realised, settled bets", ""]
    if len(rel):
        lines += ["| entry win prob | n | predicted | realised | cost | PnL |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in rel.itertuples():
            lines.append(f"| {row.bucket} | {row.n:,} | {row.predicted:.3f} | "
                         f"{row.realised:.3f} | {_usd(row.cost_usd)} | {_usd(row.pnl_usd)} |")
    else:
        lines.append("No settled bets to score.")
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


def run_name(result: sim.Result, tag: str = "") -> str:
    """`<model>__<type>`, then the account unless it is `max`, then the tag."""
    name = f"{result.model}__{result.type}"
    if result.account != "max":
        name += f"_{result.account}"
    return f"{name}_{tag}" if tag else name


def write_run(result: sim.Result, tag: str = "", m: dict | None = None) -> dict:
    """Ledger to Parquet, report to Markdown. Returns the paths written."""
    name = run_name(result, tag)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fills = fills_frame(result).assign(run_id=run_id).to_dict("records")
    bets = bets_frame(result).assign(run_id=run_id).to_dict("records")
    paths = {"fills": ledger_schema.write_ledger(fills, "fills", name),
             "bets": ledger_schema.write_ledger(bets, "bets", name)}
    REPORTS.mkdir(parents=True, exist_ok=True)
    cycles = result.cycles
    window = (f"{data.cycle_date(cycles[0])}_{data.cycle_date(cycles[-1])}"
              if cycles else "empty")
    paths["report"] = REPORTS / f"{name}_{window}.md"
    paths["report"].write_text(report_markdown(result, m))
    return paths
