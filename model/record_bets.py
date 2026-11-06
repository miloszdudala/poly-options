"""Record edge_taker's Polymarket weekly hit bets at the moment they are decided.

    .venv/bin/python model/record_bets.py

Run by launchd every five minutes (`scheduling/com.polyoptions.recordbets.plist`).
It does nothing unless the collector has finished a new session cycle. For that
cycle it prices the books, runs the model, appends the result to a separate git
repository, commits and pushes. The push time is the proof: GitHub stamps it,
and it comes before the market resolves.

The record is the opposite of `model/store/ledger`, which is rebuilt from
scratch every night by the current code:

* Rows are only ever added. A later change of rules cannot alter them, and a
  rerun of the simulation does not read or write them.
* A missed cycle stays missed. Only the newest cycle is recorded, and only
  while its books are fresh, so a laptop that slept leaves a visible gap.
* What the book holds comes from the record itself, not from the simulation.

Same pricing, model, caps and fill rules as the simulation, with one difference
the session forces: the collector fetches 5-minute bars after the close, so this
fetches today's bars itself, in memory. Without them today's touches are
invisible (`daily.bars_cover`). A ticker whose bars have not reached the cycle
is left out of that cycle. Nothing here writes to `data/store` or `model/store`,
so the simulation's results cannot move.
"""
from __future__ import annotations

import csv
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see model/data.py

import data  # noqa: E402
import sim  # noqa: E402
from config import underlyings  # noqa: E402
from engine import price_cycle  # noqa: E402
from models.edge_taker import EdgeTaker  # noqa: E402

BETS_REPO = HERE.parent.parent / "poly-options-bets"
TYPE = "poly-hit-weekly-stock"
STOCK_TIERS = ("tier1", "tier2")
PERIOD = "weekly"
MAX_BOOK_AGE = pd.Timedelta(minutes=15)   # older than the next cycle: no longer a live decision
BARS_LAG = pd.Timedelta(minutes=10)       # as daily.BARS_LAG: bars are stamped at their start

BET_COLUMNS = ["recorded_at_utc", "cycle_id", "book_seen_at_utc", "model", "type", "code",
               "market_id", "question", "ticker", "kind", "barrier", "deadline_utc", "side",
               "shares", "avg_price", "cost_usd", "fee_usd", "best_ask", "limit_cost",
               "win_prob", "value_per_share", "ev_usd", "roi", "fair_source", "spot"]
CYCLE_COLUMNS = ["recorded_at_utc", "cycle_id", "book_seen_at_utc", "model", "type", "code",
                 "markets_priced", "tickers_left_out", "offers_clearing_bar", "bets", "cash_usd"]


def stocks() -> list[str]:
    return [u.symbol for u in underlyings() if u.tier in STOCK_TIERS]


# ---------------------------------------------------------------- the record


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def append_rows(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Add rows to the end of a CSV. There is deliberately no way to edit one."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def recorded_bets(repo: Path) -> list[dict]:
    return [row for path in sorted((repo / "bets").glob("*.csv")) for row in read_rows(path)]


def book_from_record(rows: list[dict], params: sim.Params) -> sim.Portfolio:
    """The open book and the cash, from the recorded bets alone."""
    portfolio = sim.Portfolio(params)
    for row in rows:
        outcome = sim.YES if row["side"] == "YES" else sim.NO
        cost = float(row["cost_usd"])
        portfolio.open[(row["market_id"], outcome)] = sim.Position(
            market_id=row["market_id"], outcome_index=outcome, ticker=row["ticker"],
            contract_type=row["kind"], barrier=float(row["barrier"]),
            deadline=pd.Timestamp(row["deadline_utc"]), period=PERIOD,
            entry_cycle_id=row["cycle_id"], entry_at=pd.Timestamp(row["book_seen_at_utc"]),
            entry_fair=float("nan"), fair_source=row["fair_source"],
            shares=float(row["shares"]), cost_usd=cost, fee_usd=float(row["fee_usd"]))
        portfolio.cash -= cost
    return portfolio


# ---------------------------------------------------------------- live bars


def fetch_todays_bars(symbols: list[str]) -> pd.DataFrame:
    """Today's 5-minute bars from the collector's source, in the store's shape."""
    import yfinance as yf

    frames = []
    for symbol in symbols:
        try:
            bars = yf.Ticker(symbol).history(period="1d", interval="5m", auto_adjust=False)
        except Exception as exc:     # one bad ticker must not lose the other bets
            print(f"  bars failed for {symbol}: {exc}", file=sys.stderr)
            continue
        if bars.empty:
            continue
        frames.append(pd.DataFrame({
            "symbol": symbol, "bar_time": bars.index.tz_convert("UTC"),
            "open": bars["Open"].values, "high": bars["High"].values,
            "low": bars["Low"].values, "close": bars["Close"].values}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def bars_with_today(stored: pd.DataFrame, today: pd.DataFrame) -> pd.DataFrame:
    """The stored bars plus today's, the fresh fetch winning any overlap."""
    if today.empty:
        return stored
    both = pd.concat([stored, today], ignore_index=True)
    return (both.drop_duplicates(subset=["symbol", "bar_time"], keep="last")
                .sort_values(["bar_time", "symbol"], kind="stable")
                .reset_index(drop=True))


def tickers_covered(today: pd.DataFrame, symbols: list[str], stamp: pd.Timestamp) -> list[str]:
    """The tickers whose bars have reached this cycle (`daily.bars_cover`, per ticker)."""
    if today.empty:
        return []
    newest = today.groupby("symbol")["bar_time"].max()
    return [s for s in symbols if s in newest.index and newest[s] >= stamp - BARS_LAG]


# ----------------------------------------------------------------- the model


def decide(cycle_id: str, fair: pd.DataFrame, books: pd.DataFrame, tickers: list[str],
           rows: list[dict]) -> dict:
    """Run the model on one cycle, limited to this type's markets."""
    fair = fair[(fair["period"] == PERIOD) & fair["ticker"].isin(tickers)]
    books = books[(books["period"] == PERIOD) & books["ticker"].isin(tickers)]
    model = EdgeTaker()
    portfolio = book_from_record(rows, model.params)
    now = sim._cycle_time(books, fair, cycle_id)
    sim.settle(portfolio, data.resolutions_upto(now), now)
    state = sim.CycleState(cycle_id=cycle_id, now=now, books=books, fair=fair,
                           resolutions=None, portfolio=portfolio, betas={}, spy_intraday=0.0,
                           params=model.params, loader=None)
    orders = list(model.propose(state))
    fills, _ = sim.execute(state, orders)
    return {"now": now, "fair": fair, "taken": model.taken, "fills": fills,
            "cash": portfolio.cash, "offers": len(orders)}


def bet_rows(result: dict, cycle_id: str, recorded_at: str, code: str,
             questions: dict[str, str]) -> list[dict]:
    """One row per bet, however many levels of the book it took."""
    rows = []
    for take in result["taken"]:
        bet = take.bet
        outcome = sim.YES if bet.side == "YES" else sim.NO
        fills = [f for f in result["fills"]
                 if f.market_id == bet.market_id and f.outcome_index == outcome]
        if not fills:
            continue
        shares = sum(f.shares for f in fills)
        cost = sum(f.cost_usd for f in fills)
        row = sim._row_for(result["fair"], bet.market_id)
        rows.append({
            "recorded_at_utc": recorded_at, "cycle_id": cycle_id,
            "book_seen_at_utc": pd.Timestamp(result["now"]).isoformat(),
            "model": "edge_taker", "type": TYPE, "code": code,
            "market_id": bet.market_id, "question": questions.get(bet.market_id, ""),
            "ticker": bet.ticker, "kind": bet.kind, "barrier": float(row["barrier"]),
            "deadline_utc": pd.Timestamp(row["deadline_utc"]).isoformat(), "side": bet.side,
            "shares": round(shares, 6),
            "avg_price": round(sum(f.price * f.shares for f in fills) / shares, 6),
            "cost_usd": round(cost, 6), "fee_usd": round(sum(f.fee_usd for f in fills), 6),
            "best_ask": bet.ask, "limit_cost": round(take.limit, 6),
            "win_prob": round(bet.win_prob, 6), "value_per_share": round(bet.value, 6),
            "ev_usd": round(bet.value * shares - cost, 6), "roi": round(bet.roi, 6),
            "fair_source": bet.fair_source, "spot": float(row["spot"])})
    return rows


def questions_for(market_ids: list[str]) -> dict[str, str]:
    """Each market's own wording, so a reader can find it on Polymarket."""
    if not market_ids:
        return {}
    ids = ", ".join(f"'{m}'" for m in market_ids)
    frame = data._q(f"select distinct market_id, question from {data._src('poly_markets')} "
                    f"where market_id in ({ids})")
    return dict(zip(frame["market_id"], frame["question"]))


# ----------------------------------------------------------------------- git


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)
    return done.stdout.strip()


def code_version() -> str:
    """The commit of the code that decided, `-dirty` if it had uncommitted edits."""
    return git(HERE.parent, "describe", "--always", "--dirty")


def publish(repo: Path, message: str | None) -> None:
    """Commit what was added, if anything, and push whatever GitHub lacks.

    A failed push is only logged: the commit waits for the next run, and the
    late push time is what a reader then sees. With no remote yet, the commits
    wait the same way.
    """
    if message:
        git(repo, "add", "-A")
        git(repo, "commit", "-m", message)
    if git(repo, "remote") and git(repo, "rev-list", "--count", "@{u}..HEAD") != "0":
        try:
            git(repo, "push")
        except subprocess.CalledProcessError as exc:
            print(f"  push failed, will retry: {exc.stderr}", file=sys.stderr)


# ---------------------------------------------------------------------- main


def newest_new_cycle(repo: Path, now: pd.Timestamp) -> str | None:
    """The newest finished session cycle, if it is fresh and not yet recorded."""
    date = now.strftime("%Y-%m-%d")
    finished = set(data.runs(date, jobs=("snapshot",))["cycle_id"])
    cycles = [c for c in data.session_cycles(date) if c in finished]
    if not cycles:
        return None
    latest = cycles[-1]
    if pd.Timestamp(data.cycle_start(latest)) < now - MAX_BOOK_AGE:
        return None
    done = {row["cycle_id"] for row in read_rows(repo / "cycles.csv")}
    return None if latest in done else latest


def record(repo: Path = BETS_REPO) -> int:
    started = pd.Timestamp.now(tz="UTC")
    cycle_id = newest_new_cycle(repo, started)
    if cycle_id is None:
        publish(repo, None)
        return 0

    symbols = stocks()
    today = fetch_todays_bars(symbols)
    tickers = tickers_covered(today, symbols, pd.Timestamp(data.cycle_start(cycle_id)))
    stored = data.bars_5m()
    data.bars_5m = lambda: bars_with_today(stored, today)   # this process only

    fair, _ = price_cycle(cycle_id)
    result = decide(cycle_id, fair, data.books(cycle_id), tickers, recorded_bets(repo))

    recorded_at = pd.Timestamp.now(tz="UTC").isoformat()
    code = code_version()
    bought = sorted({f.market_id for f in result["fills"]})
    bets = bet_rows(result, cycle_id, recorded_at, code, questions_for(bought))
    date = data.cycle_date(cycle_id)
    append_rows(repo / "bets" / f"{date}.csv", BET_COLUMNS, bets)
    append_rows(repo / "cycles.csv", CYCLE_COLUMNS, [{
        "recorded_at_utc": recorded_at, "cycle_id": cycle_id,
        "book_seen_at_utc": pd.Timestamp(result["now"]).isoformat(),
        "model": "edge_taker", "type": TYPE, "code": code,
        "markets_priced": len(result["fair"]),
        "tickers_left_out": " ".join(sorted(set(symbols) - set(tickers))),
        "offers_clearing_bar": result["offers"], "bets": len(bets),
        "cash_usd": round(result["cash"], 2)}])
    publish(repo, f"{cycle_id}: {len(bets)} bet(s)")
    print(f"{cycle_id}: {len(bets)} bet(s), {dt.datetime.now(dt.timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(record())
