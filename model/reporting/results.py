"""What a replay did, in numbers and in words: summaries, reports, ledgers.

`replay.py` runs a replay and returns a `replay.Result`; everything here
reads one and changes nothing. Every report sets the **EV promised at entry**
against the **PnL realised**, per config (model x type x account). Scores of
a pricing step against outcomes (Brier) are in `lab.py`.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import store
from pipeline.accounts import period_key
from pipeline.offers import SIDE_INDEX
from replay import Result
from reporting import ledger_schema

REPORTS = ledger_schema.STORE.parent / "reports"
PERIOD_NOUN = {"daily": "day", "weekly": "week", "monthly": "month"}


# ------------------------------------------------------------------ frames


def bets_frame(result: Result) -> pd.DataFrame:
    """One row per bet, settled or open."""
    rows = [{
        "model": result.model, "type": result.type, "account": result.account,
        "market_id": p.market_id, "side": p.side,
        "outcome_index": SIDE_INDEX[p.side], "ticker": p.ticker,
        "contract_type": p.contract_type, "level": p.level,
        "deadline_utc": None if p.deadline is None or pd.isna(p.deadline) else str(p.deadline),
        "period": p.period, "direction": p.direction, "vol": p.vol,
        "entry_cycle_id": p.entry_cycle_id, "entry_at": p.entry_at,
        "entry_at_utc": p.entry_at.isoformat(),
        "entry_period": period_key(p.entry_at, result.period),
        "win_prob": p.win_prob, "shares": p.shares, "cost_usd": p.cost_usd,
        "fee_usd": p.fee_usd, "ev_usd": p.ev_usd, "settled": p.settled,
        "result": p.result, "payout_usd": p.payout_usd, "pnl_usd": p.pnl_usd,
        "won": None if p.payout_usd is None else p.payout_usd >= p.shares - 1e-9,
        "settled_at_utc": None if p.settled_at is None else p.settled_at.isoformat(),
    } for p in result.positions]
    return pd.DataFrame(rows)


def fills_frame(result: Result) -> pd.DataFrame:
    rows = []
    for p in result.positions:
        for f in p.fills:
            rows.append({
                "model": result.model, "type": result.type, "account": result.account,
                "market_id": p.market_id, "side": p.side,
                "outcome_index": SIDE_INDEX[p.side], "ticker": p.ticker,
                "contract_type": p.contract_type, "level": p.level,
                "deadline_utc": None if pd.isna(p.deadline) else str(p.deadline),
                "period": p.period, "cycle_id": f.cycle_id, "date": store.cycle_date(f.cycle_id),
                "filled_at_utc": f.filled_at.isoformat(), "depth_rank": f.depth_rank,
                "price": f.price, "shares": f.shares, "fee_usd": f.fee_usd,
                "cost_usd": f.cost_usd, "ev_usd": f.ev_usd, "win_prob": p.win_prob,
                "level_size": f.level_size})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- numbers


def summary(result: Result) -> dict:
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


def by_period(result: Result) -> pd.DataFrame:
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


def period_table(result: Result) -> list[str]:
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


def report_markdown(result: Result, m: dict | None = None) -> str:
    m = m or summary(result)
    cycles = result.cycles
    start = store.cycle_date(cycles[0]) if cycles else "-"
    end = store.cycle_date(cycles[-1]) if cycles else "-"
    lines = [
        f"# {result.name} ({result.model}, {result.account}) - {start} to {end}",
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


def run_name(result: Result, tag: str = "") -> str:
    """The config's name, then the tag."""
    name = result.name.replace("+", "_").replace("=", "-").replace(",", "_").replace("/", "-")
    return f"{name}_{tag}" if tag else name


def write_run(result: Result, tag: str = "", m: dict | None = None) -> dict:
    """Ledger to Parquet, report to Markdown. Returns the paths written."""
    name = run_name(result, tag)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fills = fills_frame(result).assign(run_id=run_id).to_dict("records")
    bets = bets_frame(result).assign(run_id=run_id).to_dict("records")
    paths = {"fills": ledger_schema.write_ledger(fills, "fills", name),
             "bets": ledger_schema.write_ledger(bets, "bets", name)}
    REPORTS.mkdir(parents=True, exist_ok=True)
    cycles = result.cycles
    window = (f"{store.cycle_date(cycles[0])}_{store.cycle_date(cycles[-1])}"
              if cycles else "empty")
    paths["report"] = REPORTS / f"{name}_{window}.md"
    paths["report"].write_text(report_markdown(result, m))
    return paths


# ------------------------------------------------------- the combined report


def headline_warning(rows: list[dict], sample: dict) -> str:
    """The one sentence that has to come before the PnL column is read."""
    if not sample.get("selection_biased"):
        early = sample.get("settled_early", 0)
        return ("Calibration is scored on the "
                f"{sample.get('deadline_passed', 0):,} markets past their deadline "
                f"(base rate {sample.get('base_rate', float('nan')):.3f})."
                + (f" Resolved PnL still includes {early:,} markets that settled "
                   "early by touching, whose untouched siblings are still open: "
                   "it leans toward YES winning and NO losing." if early else ""))
    resolved = sum(r.get("settled", 0) for r in rows)
    return (f"**Read the PnL columns as a coin that has not landed.** Every one "
            f"of the {sample['resolved']:,} settled markets in this window "
            f"resolved Yes and {sample['deadline_passed']:,} have reached their "
            f"deadline, because a hit market settles the instant it touches and "
            f"otherwise waits. So across the {resolved:,} settled bets "
            f"here, every NO lost and every YES won by construction, and the "
            f"differences between models below are differences in which side "
            f"they took, not in whether they were right.")


def brier_section(scores: pd.DataFrame, sample: dict) -> list[str]:
    lines = ["## Brier scores over the whole priced universe (MODELS 4.5)", "",
             "Hit markets only, model-independent: one row per market at its first "
             "priced cycle of the window, scored against its resolution. Lower is "
             "better; `brier_vs_base_rate` below zero means the estimate beats "
             "always predicting the base rate.", ""]
    if sample.get("selection_biased"):
        lines += [
            f"**This table does not mean what it looks like.** Of "
            f"{sample['markets']:,} priced markets, {sample['resolved']:,} have "
            f"settled and **{sample['deadline_passed']:,} have reached their "
            f"deadline**; the base rate is {sample['base_rate']:.3f}. A hit market "
            f"settles the instant it touches and otherwise waits for its deadline, "
            f"so a window shorter than the markets' horizons resolves nothing but "
            f"touches, and the ranking below is a ranking of which estimate is "
            f"*highest*, not which is best calibrated.", ""]
    if len(scores):
        lines += ["| estimate | n | Brier | vs base rate | mean estimate | base rate |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in scores.itertuples():
            lines.append(f"| `{row.estimate}` | {row.n:,} | {row.brier:.4f} | "
                         f"{row.brier_vs_base_rate:+.4f} | {row.mean_estimate:.3f} | "
                         f"{row.base_rate:.3f} |")
    else:
        lines.append("No priced market in the window has resolved yet.")
    return lines + [""]


def combined_report(rows: list[dict], scores: pd.DataFrame, sample: dict,
                    dates: list[str], elapsed: float) -> str:
    lines = [
        f"# All models - {dates[0]} to {dates[-1]}",
        "",
        f"{len(dates)} date(s), {elapsed:.0f}s, one row per config; each has its own report with the per-period detail.",
        "",
        "## Promised against realised",
        "",
        summary_table(rows),
        "",
        "`promised EV` is the EV at entry of every bet; `promised (settled)` and "
        "`PnL (settled)` are the same settled bets, so their ratio is "
        "`realised / promised`. Open bets are excluded from PnL (MODELS 4.3).",
        "",
        headline_warning(rows, sample),
        "",
    ]
    skipped = [r for r in rows if r.get("skipped")]
    if skipped:
        lines += ["## Markets skipped", ""]
        for r in skipped:
            parts = ", ".join(f"{k} {v:,}" for k, v in sorted(r["skipped"].items()))
            lines.append(f"- **{r['model']} / {r['type']}**: {parts}")
        lines.append("")
    lines += brier_section(scores, sample)
    for r in rows:
        if r["model"] == "random_control":
            lines.append(f"`random_control` on `{r['type']}`: {r['bets']:,} bets with no "
                         f"signal, PnL ${r['pnl']:,.2f} on {r['settled']:,} settled. "
                         "MODELS 4.5: if it earns what the models earn, the window drifted.")
    return "\n".join(lines) + "\n"
