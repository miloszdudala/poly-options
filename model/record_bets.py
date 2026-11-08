"""Add the `mvp` config's bets to proof/BETS.md at the moment they are decided,
and their results to proof/SETTLEMENTS.md once Polymarket has resolved them.

    .venv/bin/python model/record_bets.py

Run by launchd every five minutes. Bets: nothing happens unless the collector
has finished a new session cycle. A cycle with bets adds rows to the end of
the file on `main`, commits and pushes; a cycle without bets commits nothing.

Settlements: every bet in BETS.md without a settlement is looked up on
Polymarket's public API, and its resolution, not our own reading of the stock's
price, is what gets written. This needs nothing but the two files and the API.

proof/ outlives everything else in this repo. Its files depend on nothing else.
Replace this script freely when the code is rebuilt, as long as rows are only
ever added to them.

The commit goes straight onto `main` without touching the checkout, so the
record keeps going whatever branch is checked out.

The pricing is the replay's, with one difference the session forces: the
collector fetches 5-minute bars after the close, so this fetches today's bars
itself, in memory. Without them today's touches are invisible. A ticker whose
bars have not reached the cycle is left out of it. Nothing is written to the
stores, so simulation results cannot move.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO / "data" / "src"))   # see store.py

import store  # noqa: E402
import pipeline  # noqa: E402,F401  (registers the step variants)
import replay  # noqa: E402
from config import markets_cfg, underlyings  # noqa: E402
from pipeline import config as configs, markets as markets_step  # noqa: E402
from pipeline.core import Cycle, NotReady, price  # noqa: E402
from pipeline.inputs import BARS_LAG  # noqa: E402
from pipeline.offers import Bar, make_offers  # noqa: E402
from pipeline.sizing import Book  # noqa: E402

CONFIG = "mvp"
BETS = "proof/BETS.md"
SETTLEMENTS = "proof/SETTLEMENTS.md"
LAST_CYCLE = HERE / "store" / "record_last_cycle"      # not in git
MAX_BOOK_AGE = pd.Timedelta(minutes=15)                # by then the next cycle has begun
MARKET_ID_COLUMN = 3
SETTLED_ID_COLUMN = 2
GAMMA_BATCH = 100


# ------------------------------------------------------------------ the table


def table_row(p: replay.Position, written_at: str, code: str, question: str) -> str:
    cells = [_stamp(written_at), _stamp(p.entry_at), _stamp(p.deadline), p.market_id,
             question.replace("|", "/"), p.side, f"{p.shares:.2f}",
             f"{sum(f.price * f.shares for f in p.fills) / p.shares:.3f}", f"{p.cost_usd:.2f}",
             f"{p.win_prob:.1%}", f"{p.ev_usd:.2f}", code]
    return "| " + " | ".join(cells) + " |\n"


def _stamp(when) -> str:
    return pd.Timestamp(when).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")


def table_rows(text: str) -> list[list[str]]:
    """The cells of a table's rows; every row starts with its 20xx write time."""
    return [[cell.strip() for cell in line.split("|")[1:-1]]
            for line in text.splitlines() if line.startswith("| 20")]


def market_ids(text: str, column: int = MARKET_ID_COLUMN) -> set[str]:
    """The markets a table already has a row for."""
    return {row[column] for row in table_rows(text)}


# ------------------------------------------------------------------------ git


def git(repo: Path, *args: str, stdin: str | None = None, env: dict | None = None) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, input=stdin, env={**os.environ, **(env or {})})
    return done.stdout.strip()


def read_main(repo: Path, path: str = BETS) -> str:
    return subprocess.run(["git", "-C", str(repo), "show", f"main:{path}"], check=True,
                          capture_output=True, text=True).stdout


def append_to_main(repo: Path, rows: str, message: str, path: str = BETS) -> None:
    """Commit `rows` at the end of a table on `main`, whatever is checked out."""
    parent = git(repo, "rev-parse", "main")
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=read_main(repo, path) + rows)
    with tempfile.TemporaryDirectory() as folder:
        index = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        git(repo, "read-tree", "main", env=index)
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=index)
        tree = git(repo, "write-tree", env=index)
    commit = git(repo, "commit-tree", tree, "-p", parent, "-m", message)
    git(repo, "update-ref", "refs/heads/main", commit, parent)
    if git(repo, "branch", "--show-current") == "main":
        git(repo, "reset", "-q", "--", path)
        git(repo, "checkout", "--", path)


def push(repo: Path) -> None:
    """Send `main` to GitHub if it is ahead. A failure waits for the next run,
    and the late push time is what a reader then sees."""
    if git(repo, "rev-list", "--count", "origin/main..main") == "0":
        return
    try:
        git(repo, "push", "-q", "origin", "main")
    except subprocess.CalledProcessError as exc:
        print(f"  push failed, will retry: {exc.stderr}", file=sys.stderr)


# ------------------------------------------------------------------ live bars


def fetch_todays_bars(symbols: dict[str, str]) -> pd.DataFrame:
    """Today's 5-minute bars from the collector's source, in the store's shape."""
    import yfinance as yf

    frames = []
    for symbol, yf_symbol in symbols.items():
        try:
            bars = yf.Ticker(yf_symbol).history(period="1d", interval="5m", auto_adjust=False)
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


def tickers_behind(today: pd.DataFrame, symbols: list[str], stamp: pd.Timestamp) -> list[str]:
    """The tickers whose bars have not reached this cycle."""
    newest = today.groupby("symbol")["bar_time"].max() if len(today) else pd.Series(dtype=object)
    return [s for s in symbols if s not in newest.index or newest[s] < stamp - BARS_LAG]


# ------------------------------------------------------------------ the model


def decide(cycle_id: str, config: dict, held: set[str]) -> tuple[list[replay.Position], pd.Timestamp]:
    """One cycle through the replay's own steps: the new bets, and the cycle's clock."""
    fee = replay._build("fee", config)
    model = replay._build("sizing", config)
    account = replay._build("account", config)
    fills = dict(config["fills"])
    types = markets_step.contract_types(config["markets"])
    cycle = Cycle(cycle_id, types, replay._build("venue", config))
    now = cycle.now
    priced = price(cycle, config)
    offers, behind = make_offers(priced, cycle.asks, fee, Bar(**config["bar"]), fills, held, now)
    bought: dict = {}
    held = set(held)
    for order in model.decide(offers, Book(cash=account.cash())) or []:
        replay._fill(order, behind, bought, held, account, fee, fills, cycle_id, now)
    return list(bought.values()), now


def questions_for(ids: list[str]) -> dict[str, str]:
    """Each market's own wording, so a reader can find it on Polymarket."""
    quoted = ", ".join(f"'{m}'" for m in ids)
    frame = store._q(f"select distinct market_id, question from {store._src('poly_markets')} "
                     f"where market_id in ({quoted})")
    return dict(zip(frame["market_id"].astype(str), frame["question"]))


def newest_new_cycle(now: pd.Timestamp) -> str | None:
    """The newest finished session cycle, if it is fresh and not yet looked at."""
    date = now.strftime("%Y-%m-%d")
    finished = set(store.runs(date, jobs=("snapshot",))["cycle_id"])
    cycles = [c for c in store.session_cycles(date) if c in finished]
    if not cycles or pd.Timestamp(store.cycle_start(cycles[-1])) < now - MAX_BOOK_AGE:
        return None
    done = LAST_CYCLE.read_text().strip() if LAST_CYCLE.exists() else ""
    return None if cycles[-1] == done else cycles[-1]


def record() -> int:
    cycle_id = newest_new_cycle(pd.Timestamp.now(tz="UTC"))
    if cycle_id is None:
        push(REPO)
        return 0

    config = configs.load(CONFIG)
    excluded = set(config["markets"].get("exclude_tickers") or [])
    symbols = {u.symbol: u.yf_symbol for u in underlyings() if u.symbol not in excluded}
    today = fetch_todays_bars(symbols)
    stale = tickers_behind(today, list(symbols), pd.Timestamp(store.cycle_start(cycle_id)))
    config["markets"]["exclude_tickers"] = sorted(excluded | set(stale))
    stored = store.bars_5m()
    store.bars_5m = lambda: bars_with_today(stored, today)   # this process only

    try:
        bets, _ = decide(cycle_id, config, market_ids(read_main(REPO)))
    except NotReady as exc:         # SPY's bars are behind: the next run tries again
        print(f"  {exc}", file=sys.stderr)
        return 0

    if bets:
        written_at = pd.Timestamp.now(tz="UTC")
        code = git(REPO, "describe", "--always", "--dirty")
        questions = questions_for([p.market_id for p in bets])
        rows = "".join(table_row(p, written_at, code, questions.get(p.market_id, ""))
                       for p in bets)
        append_to_main(REPO, rows, f"{len(bets)} bet(s) from cycle {cycle_id}")
    push(REPO)
    LAST_CYCLE.parent.mkdir(parents=True, exist_ok=True)
    LAST_CYCLE.write_text(cycle_id)
    left_out = f", left out {' '.join(stale)}" if stale else ""
    print(f"{cycle_id}: {len(bets)} bet(s){left_out}")
    return 0


# ---------------------------------------------------------------- settlements


def unsettled(bets: str, settlements: str) -> list[list[str]]:
    """The rows of BETS.md with no row yet in SETTLEMENTS.md."""
    done = market_ids(settlements, SETTLED_ID_COLUMN)
    return [row for row in table_rows(bets) if row[MARKET_ID_COLUMN] not in done]


def closed_markets(ids: list[str]) -> dict[str, dict]:
    """Polymarket's public record of those markets that have closed."""
    url = markets_cfg()["polymarket"]["gamma_base"] + "/markets"
    found = {}
    for start in range(0, len(ids), GAMMA_BATCH):
        batch = ids[start:start + GAMMA_BATCH]
        answer = requests.get(url, params={"id": batch, "closed": "true", "limit": GAMMA_BATCH},
                              timeout=30)
        answer.raise_for_status()
        found.update({str(m["id"]): m for m in answer.json()})
    return found


def settlement_row(bet: list[str], market: dict, written_at) -> str | None:
    """A row for a resolved market; None while it is unresolved or in dispute.
    Only what BETS.md does not already say, joined on the market ID. Outcome 0
    is what a YES buys and outcome 1 what a NO buys."""
    if (market.get("umaResolutionStatus") or "").lower() != "resolved":
        return None
    prices = [float(x) for x in json.loads(market["outcomePrices"])]
    if sorted(prices) not in ([0.0, 1.0], [0.5, 0.5]):
        return None
    outcomes = json.loads(market["outcomes"])
    won = outcomes[prices.index(1.0)] if 1.0 in prices else "50-50"
    side, shares, cost = bet[5], float(bet[6]), float(bet[8])
    payout = prices[0 if side == "YES" else 1] * shares
    cells = [_stamp(written_at), _stamp(market["closedTime"]), bet[MARKET_ID_COLUMN], won,
             f"{payout:.2f}", f"{payout - cost:.2f}"]
    return "| " + " | ".join(cells) + " |\n"


def record_settlements() -> int:
    waiting = unsettled(read_main(REPO), read_main(REPO, SETTLEMENTS))
    if waiting:
        closed = closed_markets([row[MARKET_ID_COLUMN] for row in waiting])
        written_at = pd.Timestamp.now(tz="UTC")
        rows = [settlement_row(row, closed[row[MARKET_ID_COLUMN]], written_at)
                for row in waiting if row[MARKET_ID_COLUMN] in closed]
        rows = [row for row in rows if row]
        if rows:
            append_to_main(REPO, "".join(rows), f"{len(rows)} settlement(s)", SETTLEMENTS)
            print(f"{len(rows)} settlement(s)")
    push(REPO)
    return 0


def main() -> int:
    try:
        return record()
    finally:                 # a failed bet cycle must not hold up the results
        record_settlements()


if __name__ == "__main__":
    raise SystemExit(main())
