"""The live trader. Dry by default; a real order needs `--live` AND `armed: true`.

Nothing here runs by itself: no launchd job, no cron. It trades only while a
`loop` you started is running.

    .venv/bin/python live/trader.py check           # can this machine trade? sends nothing
    .venv/bin/python live/trader.py loop            # dry, one cycle a minute until the close
    .venv/bin/python live/trader.py loop --live     # the same with real orders
    .venv/bin/python live/trader.py run [--live]    # one cycle, now
    .venv/bin/python live/trader.py run --cycle 20261015T143000Z   # replay a past snapshot
    .venv/bin/python live/trader.py status          # ledger, and the wallet if a key is set
    .venv/bin/python live/trader.py setup --live    # one-off: wallet + approvals
    .venv/bin/python live/trader.py redeem --live   # collect resolved winnings
    touch live/STOP                                 # halt at the next cycle

One cycle:

1. preflight (kill switch; for --live: armed, key, geoblock, closed-only);
2. the newest collector snapshot, priced by the engine once when it appears;
   refused past `max_cycle_age_minutes`, and if it is older than
   `collect_if_older_than_minutes` the collector is started;
3. today's bars; fair values moved along their delta to the current spot;
4. the live order books; the bar (`strategy.candidates`), best ROI first;
5. per candidate: skip a market already held; skip it if its barrier printed
   since the snapshot, the forming bar included; size within the caps; walk
   the live book; send a fill-and-kill BUY;
6. record everything in `live/store/`.

Every order is fill-and-kill, so nothing ever rests in the book and there are
no open orders to cancel. There are no exits: positions are held to
resolution.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pandas as pd  # noqa: E402

import preflight  # noqa: E402
import settings as settings_mod  # noqa: E402
import strategy  # noqa: E402
from ledger import EASTERN, Ledger  # noqa: E402
from settings import KILL_SWITCH, STORE, Settings  # noqa: E402

# A fill whose reported spend or size is this far past what was asked for
# means the units are not what venue.py assumes. Halt rather than guess.
FILL_TOLERANCE = 1.05
BOOK_COLUMNS = ["market_id", "outcome_index", "ticker", "contract_type", "side",
                "depth_rank", "price", "size", "token_id"]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def best_asks(books):
    asks = books[(books["side"] == "ask") & (books["depth_rank"] == 0)]
    return asks[["market_id", "outcome_index", "ticker", "contract_type", "price",
                 "token_id"]].rename(columns={"price": "ask"})


def snapshot_book(books, market_id: str, outcome_index: int) -> dict:
    """A past cycle's stored book, shaped like `Venue.book`."""
    rows = books[(books["market_id"] == market_id) & (books["outcome_index"] == outcome_index)
                 & (books["side"] == "ask")]
    return {"asks": sorted(zip(rows["price"].astype(float), rows["size"].astype(float))),
            "min_order_size": 5.0, "condition_id": None}


def live_books(venue, snapshot_books: pd.DataFrame, market_ids) -> pd.DataFrame:
    """The venue's current asks for both tokens of `market_ids`, in the stored
    books' shape. Ticker and contract type come from the snapshot."""
    tokens = (snapshot_books[snapshot_books["market_id"].isin(set(market_ids))]
              [["market_id", "outcome_index", "ticker", "contract_type", "token_id"]]
              .drop_duplicates(["market_id", "outcome_index"]))
    if tokens.empty:
        return pd.DataFrame(columns=BOOK_COLUMNS)
    asks = venue.books(tokens["token_id"].astype(str).tolist())
    rows = []
    for t in tokens.itertuples():
        for rank, (price, size) in enumerate(asks.get(str(t.token_id), [])):
            rows.append({"market_id": t.market_id, "outcome_index": t.outcome_index,
                         "ticker": t.ticker, "contract_type": t.contract_type, "side": "ask",
                         "depth_rank": rank, "price": price, "size": size,
                         "token_id": str(t.token_id)})
    return pd.DataFrame(rows, columns=BOOK_COLUMNS)


def decide_and_trade(s: Settings, mode: str, cycle_id: str, fair: pd.DataFrame,
                     books: pd.DataFrame, venue, ledger: Ledger, now: dt.datetime,
                     printed_since=None, balance_usd: float | None = None,
                     echo=print) -> dict:
    """Steps 4-6 for one cycle. Returns counts for the run record.

    `books` supplies the top asks the bar is judged on and the token ids; in
    dry and live mode each candidate's book is then re-read from `venue` just
    before it is walked. In replay mode the stored book is walked instead.
    """
    run_id = uuid.uuid4().hex[:12]
    counts = {"candidates": 0, "orders": 0, "spent_usd": 0.0, "skipped": {}}

    def skip(why: str):
        counts["skipped"][why] = counts["skipped"].get(why, 0) + 1

    cands = strategy.candidates(fair, best_asks(books), s)
    counts["candidates"] = len(cands)
    held = ledger.held_markets(mode)
    open_cost = ledger.open_cost(mode, now)
    spent_today = ledger.spent_on(mode, now.astimezone(EASTERN).date())
    snapshot_at = dt.datetime.strptime(cycle_id[:16], "%Y%m%dT%H%M%SZ").replace(
        tzinfo=dt.timezone.utc)

    for c in cands.itertuples():
        if counts["orders"] >= s.max_orders_per_cycle:
            skip("max_orders_per_cycle")
            continue
        if c.market_id in held:
            skip("already_held")
            continue
        if printed_since is not None and printed_since(
                c.ticker, float(c.barrier), c.contract_type == "hit_up", snapshot_at):
            skip("printed_since_snapshot")
            continue
        room = {"market_cap": s.cap_per_market_usd,
                "bankroll": s.bankroll_usd - open_cost,
                "daily": s.daily_spend_usd - spent_today}
        if balance_usd is not None:
            room["wallet"] = balance_usd
        budget = min(room.values())
        if budget < 1.0:
            skip(f"no_room_{min(room, key=room.get)}")
            continue

        book = (snapshot_book(books, c.market_id, int(c.outcome_index))
                if mode == "replay" else venue.book(str(c.token_id)))
        takes = strategy.walk(book["asks"], float(c.limit_cost), budget, s,
                              min_shares=book["min_order_size"])
        if not takes:
            skip("book_too_thin_or_moved")
            continue
        shares = sum(t["shares"] for t in takes)
        amount = sum(t["price"] * t["shares"] for t in takes)
        all_in = sum(t["cost_usd"] for t in takes)
        max_price = max(t["price"] for t in takes)

        row = {"run_id": run_id, "mode": mode, "at_utc": now.isoformat(), "cycle_id": cycle_id,
               "market_id": c.market_id, "condition_id": book.get("condition_id"),
               "token_id": str(c.token_id), "outcome_index": int(c.outcome_index),
               "side": "YES" if int(c.outcome_index) == strategy.YES else "NO",
               "ticker": c.ticker, "contract_type": c.contract_type, "barrier": float(c.barrier),
               "deadline_utc": str(c.deadline_utc), "fair": float(c.fair),
               "win_prob": float(c.win_prob), "top_ask": float(c.ask), "ev": float(c.ev),
               "roi": float(c.roi), "limit_cost": float(c.limit_cost),
               "intended_shares": shares, "intended_usd": all_in, "max_price": max_price}

        if mode == "live":
            resp = venue.buy(str(c.token_id), amount, max_price, all_in)
            row["response"] = resp
            spent = float(resp.get("spent_usd") or 0.0) if resp.get("ok") else 0.0
            got = float(resp.get("shares") or 0.0) if resp.get("ok") else 0.0
            row["spent_usd"], row["shares"] = spent, got
            ledger.order(row)
            if spent > all_in * FILL_TOLERANCE + 0.01 or got > shares * FILL_TOLERANCE + 0.01:
                KILL_SWITCH.write_text(f"halted {now.isoformat()}: fill of {got} shares for "
                                       f"${spent} against an order of {shares:.2f} for "
                                       f"${all_in:.2f}; check units in venue.buy\n")
                echo(f"  HALT: fill does not add up, wrote {KILL_SWITCH}")
                counts["halted"] = True
                break
        else:
            spent, got = all_in, shares
            row["spent_usd"], row["shares"] = spent, got
            ledger.order(row)

        if spent > 0:
            held.add(c.market_id)
            open_cost += spent
            spent_today += spent
            if balance_usd is not None:
                balance_usd -= spent
            counts["orders"] += 1
            counts["spent_usd"] += spent
        status = "" if mode != "live" else f"  [{resp.get('status') or resp.get('code')}]"
        echo(f"  {mode:6s} {row['side']:3s} {c.ticker:5s} {c.contract_type:8s} "
             f"{float(c.barrier):>9g}  ask {float(c.ask):.3f}  fair {float(c.win_prob):.3f}  "
             f"EV {float(c.ev):+.3f}  ${spent:.2f} for {got:.1f} sh{status}")
    return counts


# ------------------------------------------------------------ the session


class Session:
    """What the loop carries from one minute to the next: the priced snapshot
    and the collector process it may have started."""

    def __init__(self, s: Settings, mode: str, venue, ledger: Ledger, echo=print):
        import pricing
        self.pricing = pricing
        self.s, self.mode, self.venue, self.ledger, self.echo = s, mode, venue, ledger, echo
        self.priced: dict | None = None
        self.collector = None

    def _collect(self, age_minutes) -> str:
        busy = self.collector is not None and self.collector.poll() is None
        due = age_minutes is None or age_minutes > self.s.collect_if_older_than_minutes
        if due and not busy:
            self.collector = self.pricing.start_snapshot(STORE / "collector.log")
            return "collector started"
        return "collector running" if busy else ""

    def tick(self, now: dt.datetime) -> dict:
        s, pricing = self.s, self.pricing
        record = {"at_utc": now.isoformat(), "mode": self.mode, "command": "cycle"}
        from venue import geoblock
        blocking = preflight.problems(s, self.mode, self.venue.has_key, geoblock,
                                      self.venue.closed_only)
        if blocking:
            return {**record, "status": "refused", "reasons": blocking}
        if not pricing.in_session(now):
            return {**record, "status": "outside_session"}

        newest = pricing.latest_cycle(now)
        age = (now - pricing.cycle_time(newest)).total_seconds() / 60 if newest else None
        note = self._collect(age)
        if newest is None or age > s.max_cycle_age_minutes:
            return {**record, "status": "stale_snapshot", "cycle_id": newest,
                    "age_minutes": age, "note": note}
        fresh = self.priced is None or self.priced["cycle_id"] != newest
        if fresh:
            self.priced = pricing.price(newest)
            bars = self.priced["bars"]
        else:
            bars = pricing.fetch_today_bars(pricing.tradeable_tickers(self.priced["fair"]))

        fair = pricing.move_fair(self.priced["fair"], bars, s.max_spot_move)
        books = live_books(self.venue, self.priced["books"], fair["market_id"].unique()
                           if not fair.empty else [])
        balance = self.venue.balance_usd() if self.mode == "live" else None
        counts = decide_and_trade(
            s, self.mode, newest, fair, books, self.venue, self.ledger, now,
            printed_since=lambda t, b, up, since: pricing.printed_since(bars, t, b, up, since),
            balance_usd=balance, echo=self.echo)
        return {**record, "status": "halted" if counts.get("halted") else "ok",
                "cycle_id": newest, "age_minutes": round(age, 1), "repriced": fresh,
                "markets": int(fair["market_id"].nunique()) if not fair.empty else 0,
                **counts}


# --------------------------------------------------------------- commands


def _mode(args) -> str:
    return "live" if getattr(args, "live", False) else "dry"


def _session(args) -> Session:
    from venue import Venue
    return Session(settings_mod.load(), _mode(args), Venue(settings_mod.secrets()), Ledger())


def _summary(r: dict) -> str:
    if r["status"] != "ok":
        extra = r.get("reasons") or r.get("note") or ""
        return f"{r['status']} {r.get('cycle_id') or ''} {extra}".rstrip()
    return (f"{r['cycle_id'][:16]} ({r['age_minutes']} min{', repriced' if r['repriced'] else ''}): "
            f"{r['markets']} markets, {r['candidates']} candidates, {r['orders']} orders "
            f"${r['spent_usd']:.2f}; skipped {r['skipped']}")


def cmd_run(args) -> int:
    if args.cycle:
        return cmd_replay(args)
    session = _session(args)
    r = session.tick(utcnow())
    session.ledger.run(r)
    print(_summary(r))
    return {"refused": 2, "halted": 3}.get(r["status"], 0)


def cmd_loop(args) -> int:
    """One cycle every `loop_seconds` until the close, the kill switch or Ctrl-C.
    Started before the open, it waits for it."""
    session = _session(args)
    s = session.s
    print(f"{session.mode} loop, every {s.loop_seconds:.0f} s. Ctrl-C or `touch {KILL_SWITCH}` to stop.")
    was_open = False
    try:
        while True:
            started = time.monotonic()
            now = utcnow()
            r = session.tick(now)
            if r["status"] == "outside_session":
                if was_open:
                    print(f"{now:%H:%M:%S} the session has closed; stopping")
                    break
            else:
                was_open = True
                session.ledger.run(r)
                print(f"{now.astimezone(EASTERN):%H:%M:%S} ET  {_summary(r)}", flush=True)
            if r["status"] in ("refused", "halted"):
                return 2 if r["status"] == "refused" else 3
            time.sleep(max(0.0, s.loop_seconds - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_replay(args) -> int:
    """`run --cycle`: a past snapshot, its stored books, no venue, no orders."""
    import pricing
    s = settings_mod.load()
    ledger = Ledger()
    now = pricing.cycle_time(args.cycle)
    blocking = preflight.problems(s, "replay", False, None, None)
    if blocking:
        print("\n".join(f"refused: {p}" for p in blocking))
        return 2
    priced = pricing.price(args.cycle, fresh_bars=False)
    counts = decide_and_trade(s, "replay", args.cycle, priced["fair"], priced["books"],
                              None, ledger, now)
    ledger.run({"at_utc": utcnow().isoformat(), "mode": "replay", "command": "run",
                "status": "ok", "cycle_id": args.cycle, **counts})
    print(f"{counts['candidates']} candidates, {counts['orders']} orders, "
          f"${counts['spent_usd']:.2f}; skipped {counts['skipped']}")
    return 0


def cmd_check(args) -> int:
    """Everything `loop --live` checks, plus the wallet and the data. Sends nothing."""
    s = settings_mod.load()
    from venue import Venue, geoblock
    venue = Venue(settings_mod.secrets())
    try:
        geo = geoblock()
        print(f"geoblock      {'BLOCKED' if geo.get('blocked') else 'ok'}  "
              f"country={geo.get('country')} ip={geo.get('ip')}")
    except Exception as exc:
        print(f"geoblock      check failed: {exc}")
    print(f"armed         {s.armed}")
    print(f"kill switch   {'PRESENT' if KILL_SWITCH.exists() else 'absent'}")
    print(f"private key   {'set' if venue.has_key else 'missing'}")
    if venue.has_key:
        try:
            print(f"wallet        {venue.wallet()}")
            print(f"balance       ${venue.balance_usd():.2f} pUSD")
            print(f"closed-only   {venue.closed_only()}")
            print(f"approvals     {venue.approvals()}")
        except Exception as exc:
            print(f"account       check failed: {exc}")
    import pricing
    now = utcnow()
    newest = pricing.latest_cycle(now)
    print(f"session       {'open' if pricing.in_session(now) else 'closed'}")
    print(f"snapshot      {newest or 'none today'}"
          + (f" ({(now - pricing.cycle_time(newest)).total_seconds() / 60:.0f} min old)"
             if newest else ""))
    problems = preflight.problems(s, "live", venue.has_key, geoblock, venue.closed_only)
    print("\nlive trading: " + ("READY" if not problems else "NOT READY"))
    for p in problems:
        print(f"  - {p}")
    return 0 if not problems else 1


def cmd_status(args) -> int:
    s = settings_mod.load()
    ledger = Ledger()
    now = utcnow()
    today = now.astimezone(EASTERN).date()
    for mode in ("dry", "live"):
        fills = ledger.fills(mode)
        print(f"{mode}: {len(fills)} fills, ${sum(f['spent_usd'] for f in fills):.2f} bought, "
              f"open ${ledger.open_cost(mode, now):.2f} of ${s.bankroll_usd:.0f}, "
              f"today ${ledger.spent_on(mode, today):.2f} of ${s.daily_spend_usd:.0f}")
    from venue import Venue
    venue = Venue(settings_mod.secrets())
    if venue.has_key:
        print(f"\nwallet {venue.wallet()}  balance ${venue.balance_usd():.2f}")
        for p in venue.positions():
            print(f"  {p.title[:60]:60s} {p.outcome:4s} {float(p.current_size):8.2f} sh  "
                  f"cost ${float(p.total_cost_usdc or 0):7.2f}  now ${float(p.current_value or 0):7.2f}  "
                  f"{p.status}")
    return 0


def cmd_setup(args) -> int:
    """Derive API credentials, create the Deposit Wallet, approve the exchange
    contracts. Spends no collateral, but it does write to the chain."""
    if not args.live:
        print("setup writes approvals on-chain; re-run with --live to do it")
        return 2
    from venue import Venue, geoblock
    geo = geoblock()
    if geo.get("blocked", True):
        print(f"refused: blocked from {geo.get('country')}")
        return 2
    venue = Venue(settings_mod.secrets())
    print(f"wallet {venue.wallet()}  (fund this address)")
    venue.setup_approvals()
    print(f"approvals {venue.approvals()}")
    return 0


def cmd_redeem(args) -> int:
    if not args.live:
        print("redeem sends transactions; re-run with --live")
        return 2
    from venue import Venue
    venue = Venue(settings_mod.secrets())
    ledger = Ledger()
    for p in venue.positions(status="REDEEMABLE"):
        print(f"redeeming {p.title[:60]} ({p.outcome}, {float(p.current_size):.2f} sh)")
        outcome = venue.redeem(str(p.condition_id))
        ledger.settled({"at_utc": utcnow().isoformat(), "condition_id": str(p.condition_id),
                        "title": p.title, "outcome": p.outcome, "result": str(outcome)})
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one cycle now")
    run.add_argument("--live", action="store_true")
    run.add_argument("--cycle", help="a past snapshot id: replay, stored books, no orders")
    sub.add_parser("loop", help="one cycle a minute until the close").add_argument(
        "--live", action="store_true")
    sub.add_parser("check")
    sub.add_parser("status")
    for name in ("setup", "redeem"):
        sub.add_parser(name).add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if getattr(args, "cycle", None) and getattr(args, "live", False):
        parser.error("--cycle replays the past and cannot be combined with --live")
    return {"run": cmd_run, "loop": cmd_loop, "check": cmd_check, "status": cmd_status,
            "setup": cmd_setup, "redeem": cmd_redeem}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
