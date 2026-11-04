"""Performance review: what each model bought, and what it is worth now.

    .venv/bin/python model/review.py                 # every bet since the start, by week and month
    .venv/bin/python model/review.py --month 2026-09 # only positions opened that month
    .venv/bin/python model/review.py --positions     # also list every open position

Weeks and months are those of the position's first fill, in New York time
(a week starts on Monday). The models are the current ones in MODEL_ORDER;
ledgers of removed models still on disk are left out.

Reads the ledgers the last `run_all.py` / `daily.py` wrote and the newest book
snapshot in the store. Nothing is replayed, so the ledgers are only as fresh
as the last run: the header says how far they reach.

Three values per position:

* **settled** - a market that has resolved pays 1, 0 or 0.5 per share. That
  is the PnL that counts.
* **@ask** - an open position marked at the ask it would cost to buy the same
  side now, the comparison between our entry price and today's price for the
  same bet. It is the fair mark for a buy-and-hold book, because the bid-ask
  spread is never paid on a position held to resolution.
* **@bid (sell all)** - what selling every open share now would raise, walking
  down the bid ladder and paying the taker fee `0.04 * min(p, 1-p)`. Shares
  beyond the visible depth are worth nothing. It is a floor, not a forecast:
  these books are thin and wide, and the strategy does not exit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))

import data  # noqa: E402
import sim  # noqa: E402

LEDGER = HERE / "store" / "ledger"
MODEL_ORDER = ["edge_taker", "direction_balanced", "scenario_hedged", "random_control"]
# A book older than this behind the newest cycle is flagged: the market has
# probably stopped accepting orders and its last quote is a fossil.
STALE_AFTER = pd.Timedelta(hours=2)
EASTERN = "America/New_York"

TTY = sys.stdout.isatty()


def _color(text: str, value: float) -> str:
    if not TTY or value is None or not np.isfinite(value) or abs(value) < 0.005:
        return text
    return f"\033[{'32' if value > 0 else '31'}m{text}\033[0m"


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if TTY else text


# ------------------------------------------------------------------ inputs


def load_fills() -> pd.DataFrame:
    frames = []
    for model_dir in sorted(LEDGER.iterdir()):
        fills = model_dir / "fills"
        if fills.exists() and any(fills.glob("*.parquet")):
            frames.append(pd.read_parquet(fills))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["filled_at_utc"] = pd.to_datetime(df["filled_at_utc"], format="ISO8601", utc=True)
    return df


def positions_from(fills: pd.DataFrame) -> pd.DataFrame:
    """One row per (model, market, side): shares, all-in cost, average entry price."""
    fills = fills.assign(
        notional=fills["price"] * fills["shares"],
        # the fair of the side bought, not of YES
        side_fair=np.where(fills["outcome_index"] == sim.YES, fills["fair"], 1.0 - fills["fair"]),
    )
    fills["fair_x_shares"] = fills["side_fair"] * fills["shares"]
    pos = (fills.groupby(["model", "market_id", "outcome_index"], as_index=False)
           .agg(ticker=("ticker", "first"), contract_type=("contract_type", "first"),
                barrier=("barrier", "first"), deadline_utc=("deadline_utc", "first"),
                period=("period", "first"), opened=("filled_at_utc", "min"),
                shares=("shares", "sum"), cost=("cost_usd", "sum"), fee=("fee_usd", "sum"),
                notional=("notional", "sum"), fair_x_shares=("fair_x_shares", "sum")))
    pos["entry_price"] = pos["notional"] / pos["shares"]
    pos["entry_fair"] = pos["fair_x_shares"] / pos["shares"]
    pos["side"] = np.where(pos["outcome_index"] == sim.YES, "YES", "NO")
    return pos.drop(columns=["notional", "fair_x_shares"])


def latest_books(market_ids: list[str], since_date: str) -> pd.DataFrame:
    """Every level of the newest book we hold for each market, from any cycle."""
    ids = ", ".join(f"'{m}'" for m in market_ids)
    df = data._q(f"""
        with latest as (
            select market_id, max(cycle_id) cycle_id
            from {data._src('poly_books')}
            where date >= '{since_date}' and market_id in ({ids})
            group by 1
        )
        select b.market_id, b.outcome_index, b.side, b.depth_rank, b.price, b.size, b.cycle_id
        from {data._src('poly_books')} b join latest l using (market_id, cycle_id)
        where b.date >= '{since_date}'
    """)
    df["cycle_at"] = df["cycle_id"].map(data.cycle_start)
    return df


def newest_cycle() -> str:
    return data._q(f"select max(cycle_id) c from {data._src('poly_books')} "
                   f"where date >= '{(dt.date.today() - dt.timedelta(days=7)).isoformat()}'"
                   )["c"].iloc[0]


# --------------------------------------------------------------- valuation


def sell_value(bids: pd.DataFrame, shares: float) -> tuple[float, float]:
    """Proceeds net of fee from hitting the bid ladder best-first, and shares left unsold."""
    proceeds, left = 0.0, shares
    for row in bids.sort_values("depth_rank").itertuples():
        take = min(left, row.size)
        proceeds += take * (row.price - sim.fee_per_share(row.price))
        left -= take
        if left <= 1e-9:
            break
    return proceeds, max(left, 0.0)


def value(pos: pd.DataFrame, books: pd.DataFrame, resolutions: pd.DataFrame,
          newest: dt.datetime) -> pd.DataFrame:
    res = resolutions.set_index("market_id")
    grouped = {k: g for k, g in books.groupby(["market_id", "outcome_index"])}
    rows = []
    for p in pos.itertuples():
        out = {"status": "open", "ask": np.nan, "bid": np.nan, "payout": np.nan,
               "val_ask": np.nan, "val_sell": np.nan, "unsold": 0.0, "stale": False,
               "no_ask": False}
        if p.market_id in res.index:
            r = res.loc[p.market_id]
            payout = sim._payout_per_share(r["result"], r["outcome_prices"], p.outcome_index)
            if payout is not None:
                out |= {"status": "settled", "payout": payout,
                        "val_ask": p.shares * payout, "val_sell": p.shares * payout}
                rows.append(out)
                continue
        book = grouped.get((p.market_id, p.outcome_index))
        if book is None or book.empty:
            out["status"] = "no book"
            rows.append(out)
            continue
        asks = book[book["side"] == "ask"].sort_values("depth_rank")
        bids = book[book["side"] == "bid"]
        out["stale"] = bool(newest - book["cycle_at"].iloc[0] > STALE_AFTER)
        if len(asks):
            out["ask"] = float(asks["price"].iloc[0])
        if len(bids):
            out["bid"] = float(bids.sort_values("depth_rank")["price"].iloc[0])
        # An empty ask side is nearly always a touched market waiting to be
        # settled (bid 0.99+, nobody selling); the bid is then the price.
        out["no_ask"] = not len(asks)
        mark = out["ask"] if len(asks) else out["bid"]
        out["val_ask"] = p.shares * mark if np.isfinite(mark) else np.nan
        out["val_sell"], out["unsold"] = sell_value(bids, p.shares)
        rows.append(out)
    out = pd.concat([pos.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    out["pnl_ask"] = out["val_ask"] - out["cost"]
    out["pnl_sell"] = out["val_sell"] - out["cost"]
    out["move_c"] = np.where(out["status"] == "open",
                             (out["val_ask"] / out["shares"] - out["entry_price"]) * 100, np.nan)
    return out


# ----------------------------------------------------------------- output


def _money(x: float, width: int = 10) -> str:
    if x is None or not np.isfinite(x):
        return "-".rjust(width)
    return _color(f"{'+' if x > 0 else '-' if x < 0 else ' '}${abs(x):,.0f}".rjust(width), x)


def _pct(x: float, width: int = 7) -> str:
    if x is None or not np.isfinite(x):
        return "-".rjust(width)
    return _color(f"{x:+.1%}".rjust(width), x)


def _cents(x: float, width: int = 7) -> str:
    if x is None or not np.isfinite(x):
        return "-".rjust(width)
    return _color(f"{x:+.1f}¢".rjust(width), x)


def summarize(v: pd.DataFrame) -> dict:
    settled = v[v["status"] == "settled"]
    live = v[v["status"] == "open"]
    marked = v[v["val_ask"].notna()]
    cost = v["cost"].sum()
    w = live["shares"].where(live["move_c"].notna(), 0)
    return {
        "n": len(v), "n_open": len(live), "n_settled": len(settled),
        "n_nobook": int((v["status"] == "no book").sum()), "n_stale": int(live["stale"].sum()),
        "cost": cost,
        "pnl_settled": (settled["val_ask"] - settled["cost"]).sum(),
        "cost_open": live["cost"].sum(),
        "pnl_open_ask": live["pnl_ask"].sum(),
        "pnl_ask": marked["pnl_ask"].sum(),
        "roi_ask": marked["pnl_ask"].sum() / marked["cost"].sum() if marked["cost"].sum() else np.nan,
        "pnl_sell": v["pnl_sell"].sum(),
        "roi_sell": v["pnl_sell"].sum() / cost if cost else np.nan,
        "move_c": (live["move_c"].fillna(0) * w).sum() / w.sum() if w.sum() else np.nan,
        "frac_better": (live["move_c"] > 0).mean() if len(live) else np.nan,
        "unsold": (v["unsold"] * v["val_ask"] / v["shares"]).fillna(0).sum(),
    }


SUMMARY_HEAD = (f"{'model':<30}{'pos':>5}{'open':>6}{'cost':>10}{'settled':>10}"
                f"{'open@ask':>10}{'total@ask':>11}{'ROI':>8}"
                f"{'sell all':>10}{'ROI':>8}{'move':>8}{'better':>8}")


def summary_line(name: str, s: dict) -> str:
    better = f"{s['frac_better']:.0%}".rjust(8) if np.isfinite(s["frac_better"]) else "-".rjust(8)
    return (f"{name:<30}{s['n']:>5}{s['n_open']:>6}{'$' + format(s['cost'], ',.0f'):>10}"
            f"{_money(s['pnl_settled'])}{_money(s['pnl_open_ask'])}{_money(s['pnl_ask'], 11)}"
            f"{_pct(s['roi_ask'], 8)}"
            f"{_money(s['pnl_sell'])}{_pct(s['roi_sell'], 8)}{_cents(s['move_c'], 8)}{better}")


def print_periods(v: pd.DataFrame, models: list[str]) -> None:
    """Every model, week by week, then month by month, of the first fill."""
    opened = v["opened"].dt.tz_convert(EASTERN)
    week = opened.dt.normalize().dt.tz_localize(None) - pd.to_timedelta(opened.dt.weekday, unit="D")
    v = v.assign(week=week.dt.strftime("wk %m-%d"), month=opened.dt.strftime("%Y-%m"))
    for column, title in (("week", "By week the bet was made (Monday's date)"),
                          ("month", "By month the bet was made")):
        print(_bold(title))
        print(_bold(SUMMARY_HEAD.replace("model       ", "model / when")))
        for m in models:
            part = v[v["model"] == m]
            for label in sorted(part[column].unique()):
                print(summary_line(f"{m} {label}", summarize(part[part[column] == label])))
            print()


def print_positions(v: pd.DataFrame) -> None:
    live = v[v["status"] != "settled"].sort_values("pnl_ask")
    if live.empty:
        return
    print(f"    {'ticker':<7}{'type':<9}{'barrier':>9}{'deadline':>12}{'side':>5}"
          f"{'shares':>9}{'entry':>7}{'fair':>7}{'ask':>7}{'bid':>7}{'move':>8}"
          f"{'@ask':>9}{'sell':>9}")
    for p in live.itertuples():
        flag = (" stale" if p.stale else " NO BOOK" if p.status == "no book"
                else " no ask, marked at bid" if p.no_ask else "")
        fmt = lambda x: f"{x:.3f}".rjust(7) if np.isfinite(x) else "-".rjust(7)  # noqa: E731
        print(f"    {p.ticker:<7}{p.contract_type:<9}{p.barrier:>9g}{str(p.deadline_utc)[:10]:>12}"
              f"{p.side:>5}{p.shares:>9,.0f}{fmt(p.entry_price)}{fmt(p.entry_fair)}"
              f"{fmt(p.ask)}{fmt(p.bid)}{_cents(p.move_c, 8)}{_money(p.pnl_ask, 9)}"
              f"{_money(p.pnl_sell, 9)}{flag}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", default=None,
                        help="only positions first opened in this month, YYYY-MM "
                             "(default: everything since the start)")
    parser.add_argument("--positions", action="store_true", help="list every open position")
    args = parser.parse_args(argv)

    fills = load_fills()
    if fills.empty:
        print("No ledgers in model/store/ledger. Run model/run_all.py first.", file=sys.stderr)
        return 1
    run_ids = sorted(fills["run_id"].dropna().unique())
    last_fill = fills["filled_at_utc"].max()

    fills = fills[fills["model"].isin(MODEL_ORDER)]
    pos = positions_from(fills)
    if args.month:
        start = pd.Timestamp(f"{args.month}-01", tz=EASTERN)
        pos = pos[(pos["opened"] >= start) & (pos["opened"] < start + pd.offsets.MonthBegin(1))]
        scope = f"positions opened in {args.month}"
    else:
        start = pos["opened"].min()
        scope = f"every position since {start.tz_convert(EASTERN):%Y-%m-%d}"
    if pos.empty:
        print(f"No positions opened in {args.month}.")
        return 0

    newest = newest_cycle()
    newest_at = data.cycle_start(newest)
    books = latest_books(sorted(pos["market_id"].unique()),
                         start.tz_convert("UTC").date().isoformat())
    resolutions = data.resolutions_upto(dt.datetime.now(dt.timezone.utc))
    v = value(pos, books, resolutions, newest_at)

    print(_bold(f"Performance review — {scope}"))
    print(f"ledgers: run {run_ids[0]}{' … ' + run_ids[-1] if len(run_ids) > 1 else ''}, "
          f"fills through {last_fill:%Y-%m-%d %H:%MZ}")
    print(f"prices:  newest book cycle {newest_at:%Y-%m-%d %H:%MZ}; "
          f"resolutions as of now")
    if newest_at - last_fill > pd.Timedelta(days=1):
        print("         (ledgers are older than the books: run model/run_all.py "
              "to include the latest days' bets)")
    print()

    print(_bold(SUMMARY_HEAD))
    models = [m for m in MODEL_ORDER if m in set(v["model"])]
    stats = {m: summarize(v[v["model"] == m]) for m in models}
    for m in models:
        print(summary_line(m, stats[m]))
    print()
    print("settled   PnL on resolved markets (payout - cost incl. fee)")
    print("open@ask  open positions marked at today's ask for the same side: today's")
    print("          price of the bet against what we paid (no spread is paid when held)")
    print("          (a market with no ask left is marked at its bid)")
    print("total@ask settled + open@ask;  ROI on cost")
    print("sell all  every open share hit into the bid ladder now, net of taker fee;")
    print("          shares past visible depth count as 0. A floor, not the strategy")
    print("move      share-weighted (ask now - entry price) on open positions, ¢/share")
    print("better    share of open positions whose ask is above our entry")
    print()

    print(_bold("By side"))
    print(_bold(SUMMARY_HEAD.replace("model       ", "model / side")))
    for m in models:
        for side in ("YES", "NO"):
            part = v[(v["model"] == m) & (v["side"] == side)]
            if len(part):
                print(summary_line(f"{m} {side}", summarize(part)))
    print()

    print_periods(v, models)

    caveats = []
    nobook = int((v["status"] == "no book").sum())
    stale = int(((v["status"] == "open") & v["stale"]).sum())
    if nobook:
        caveats.append(f"{nobook} position(s) have no book and no resolution; left out of @ask, "
                       f"counted as 0 in sell all")
    if stale:
        caveats.append(f"{stale} open position(s) priced from a book more than "
                       f"{STALE_AFTER.components.hours}h older than the newest cycle")
    unsold = sum(s["unsold"] for s in stats.values())
    if unsold > 1:
        caveats.append(f"sell all leaves ${unsold:,.0f} of @ask value unsold past visible bid depth")
    for c in caveats:
        print(f"note: {c}")
    if caveats:
        print()

    if args.positions:
        for m in models:
            part = v[v["model"] == m]
            if (part["status"] != "settled").any():
                print(_bold(f"{m} — open positions, worst @ask first"))
                print_positions(part)
                print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
