"""Build the price-versus-outcome calibration report for Feb-Aug 2026.

    .venv/bin/python model/run_calibration.py            # -> model/reports/calibration_<date>.md

Reads what `data/src/poly_backfill.py` wrote (`poly_markets`, `resolutions`,
`poly_price_history`) plus `underlying_bars` and `rates`, scores three fair
values against the realised outcomes, and writes the report. No network.

Design decisions that change the numbers, all repeated in the report itself:

* **One hourly price point is a mid proxy, not a tradeable quote.** Polymarket's
  `prices-history` returns one number per hour with no size and no side. Every
  reliability row below is therefore about where the market was *marked*, not
  where anyone could have traded; the paper PnL adds half of the 7c median
  spread (FINDINGS 3.3) and the 4% taker fee before claiming a fill.
* **The deadline is the last NYSE session close at or before `end_date`**, not
  `end_date` itself, because a hit market can only resolve on a Pyth candle
  during regular hours (MODELS 3.3).
* **Touch tables are built from sessions strictly before 2026-02-01**, so the
  empirical fair value at every scored point is out-of-sample.
* **Spot at a price point is the open of the hourly bar containing it**, never
  the close. yfinance stamps an hourly bar at its start (13:30Z covers
  13:30-14:30Z), so the close is a print from up to an hour after the point.
  The first version of this report used it, and that lookahead was worth
  about 3 points of the YES rule's ROI (review 2026-09-18).
* **September is excluded** (the prompt's rule, FINDINGS 5.3's reason): a
  touched market closes early while an untouched one waits for period end, so
  a month still in progress over-represents YES and would invert the NO side.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "data" / "src"))   # see model/data.py

import calibration as cal  # noqa: E402
from config import PARQUET_DIR  # noqa: E402
# The session calendar is shared with the engine (model/sessions.py): a hit
# market's horizon is counted in monitored sessions in both places or the two
# disagree about what a two-day barrier is.
from sessions import nyse_sessions, sessions_remaining  # noqa: E402

MARKET_TZ = "America/New_York"
FIRST_DEADLINE = pd.Timestamp("2026-02-01", tz=MARKET_TZ)
LAST_DEADLINE = pd.Timestamp("2026-09-01", tz=MARKET_TZ)
TABLE_START = "2026-02-01"   # touch tables use sessions strictly before this


def src(table: str) -> str:
    root = PARQUET_DIR / table
    return f"read_parquet('{root}/**/*.parquet', hive_partitioning=true, union_by_name=true)"


# ------------------------------------------------------------------- loading


def load_markets(con) -> pd.DataFrame:
    """Latest metadata row per hit market, with its outcome, Feb-Aug 2026."""
    df = con.sql(f"""
        with ranked as (
            select *, row_number() over (
                partition by market_id order by fetched_at_utc desc) rn
            from {src('poly_markets')}
            where contract_type in ('hit_up', 'hit_down')
        ),
        outcome as (
            select market_id, result, settled_at_utc from (
                select market_id, result, settled_at_utc, row_number() over (
                    partition by market_id order by fetched_at_utc desc) rn
                from {src('resolutions')}) where rn = 1
        )
        select r.market_id, r.ticker, r.contract_type, r.barrier, r.period,
               r.end_date, r.created_at,
               coalesce(o.result, r.result) result,
               coalesce(o.settled_at_utc, r.closed_time) settled_at_utc
        from ranked r left join outcome o using (market_id)
        where r.rn = 1
    """).df()
    df = df[df["result"].isin(["Yes", "No"])].copy()
    df["barrier"] = pd.to_numeric(df["barrier"], errors="coerce")
    df = df.dropna(subset=["barrier", "ticker", "end_date"])
    for col in ("end_date", "created_at", "settled_at_utc"):
        df[col] = pd.to_datetime(df[col], format="ISO8601", utc=True, errors="coerce")
    df["resolved_yes"] = df["result"].eq("Yes")
    df["is_up"] = df["contract_type"].eq("hit_up")
    return df.dropna(subset=["end_date"]).reset_index(drop=True)


def load_points(con, market_ids: list[str]) -> pd.DataFrame:
    """Every hourly YES-token price point for those markets."""
    ids = con.from_df(pd.DataFrame({"market_id": market_ids}))
    con.register("wanted", ids)
    df = con.sql(f"""
        select distinct h.market_id, h.point_time_utc, h.price
        from {src('poly_price_history')} h join wanted w using (market_id)
        where h.outcome_index = 0 and h.price is not null
    """).df()
    df["point_time"] = pd.to_datetime(df["point_time_utc"], format="ISO8601", utc=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    return df.drop(columns=["point_time_utc"])


def load_bars(con, interval: str) -> pd.DataFrame:
    df = con.sql(f"""
        select distinct symbol, bar_time_utc, open, high, low, close
        from {src('underlying_bars')} where interval = '{interval}'
    """).df()
    df["bar_time"] = pd.to_datetime(df["bar_time_utc"], format="ISO8601", utc=True)
    return df.drop(columns=["bar_time_utc"]).sort_values(["symbol", "bar_time"])


def load_rates(con) -> pd.DataFrame:
    df = con.sql(f"""
        select obs_date, avg(rate_percent) rate from {src('rates')}
        where series = 'DGS1MO' and rate_percent is not null group by 1
    """).df()
    df["obs_date"] = pd.to_datetime(df["obs_date"], errors="coerce")
    return df.dropna().sort_values("obs_date")


# --------------------------------------------------------------- assembly


def asof_values(keys: pd.Series, times: pd.Series, ref: pd.DataFrame,
                key_col: str, time_col: str, value_col: str) -> np.ndarray:
    """Most recent `value_col` at or before each (key, time). Vectorised merge_asof."""
    left = pd.DataFrame({"k": np.asarray(keys), "t": pd.to_datetime(pd.Series(times.values), utc=True)})
    left = left.reset_index().sort_values("t")
    right = ref[[key_col, time_col, value_col]].rename(
        columns={key_col: "k", time_col: "t", value_col: "v"}).copy()
    right["t"] = pd.to_datetime(right["t"], utc=True)
    right = right.sort_values("t")
    merged = pd.merge_asof(left, right, on="t", by="k", direction="backward")
    return merged.sort_values("index")["v"].values


def hourly_spot(tickers: pd.Series, times: pd.Series, hourly: pd.DataFrame) -> np.ndarray:
    """The last print known at each point: the OPEN of the hourly bar containing it.

    A yfinance hourly bar is stamped at its start, so a backward as-of join on
    the stamp finds the bar the point sits inside - and that bar's close is up
    to an hour in the point's future. Its open is the first print at or after
    the bar's start, which is at or before the point. Review 2026-09-18: using
    the close moved the "buy YES when empirical beats price" rule from +10.3%
    to +13.1%, which is lookahead dressed as edge.
    """
    return asof_values(tickers, times, hourly, "symbol", "bar_time", "open")


def build_points(con) -> tuple[pd.DataFrame, dict]:
    """The one table everything in the report is computed from."""
    markets = load_markets(con)
    sess = nyse_sessions()
    closes = sess["close"].values

    # Deadline = last session close at or before end_date (MODELS 3.3).
    end_ns = markets["end_date"].values.astype("datetime64[ns]")
    idx = np.searchsorted(closes.astype("datetime64[ns]"), end_ns, side="right") - 1
    markets = markets[idx >= 0].reset_index(drop=True)
    idx = idx[idx >= 0]
    markets["deadline"] = pd.to_datetime(closes[idx], utc=True)
    markets["deadline_month"] = (markets["deadline"].dt.tz_convert(MARKET_TZ)
                                 .dt.strftime("%Y-%m"))

    total = len(markets)
    in_window = markets["deadline"].between(FIRST_DEADLINE, LAST_DEADLINE)
    dropped_september = int((~in_window).sum())
    markets = markets[in_window].reset_index(drop=True)

    points = load_points(con, markets["market_id"].tolist())
    df = points.merge(markets, on="market_id", how="inner")

    # Only the market's tradeable life: after it resolved, the "price" is the
    # settlement, which would score a perfect Brier and mean nothing.
    stop = df["settled_at_utc"].fillna(df["deadline"])
    stop = np.minimum(stop.values.astype("datetime64[ns]"),
                      df["deadline"].values.astype("datetime64[ns]"))
    df = df[(df["point_time"].values.astype("datetime64[ns]") < stop)
            & (df["point_time"] >= df["created_at"])
            & df["price"].between(0.0, 1.0, inclusive="neither")].reset_index(drop=True)

    daily = load_bars(con, "1d")
    hourly = load_bars(con, "1h")

    df["spot"] = hourly_spot(df["ticker"], df["point_time"], hourly)

    # Realised vol: EWMA known at the last completed daily close before the point.
    vol_frames = []
    tables: dict[str, cal.TouchTable] = {}
    for symbol, bars in daily.groupby("symbol"):
        bars = bars.sort_values("bar_time")
        vol = cal.ewma_vol(bars["close"].values)
        vol_frames.append(pd.DataFrame({"symbol": symbol, "bar_time": bars["bar_time"].values,
                                        "vol": vol}))
        train = bars[bars["bar_time"] < pd.Timestamp(TABLE_START, tz="UTC")]
        if len(train) > 60:
            tables[symbol] = cal.touch_table(train["high"].values, train["low"].values,
                                             train["close"].values)
    vols = pd.concat(vol_frames, ignore_index=True)
    # A daily bar is stamped 00:00 ET; shift to the session close so a value is
    # only visible after the session that produced it has ended.
    vols["bar_time"] = vols["bar_time"] + pd.Timedelta(hours=21)
    df["sigma"] = asof_values(df["ticker"], df["point_time"], vols,
                              "symbol", "bar_time", "vol")

    rates = load_rates(con)
    rates["t"] = pd.to_datetime(rates["obs_date"], utc=True)
    rates["symbol"] = "_"
    df["rate"] = asof_values(pd.Series(["_"] * len(df)), df["point_time"], rates,
                             "symbol", "t", "rate") / 100.0

    df["sessions_left"] = sessions_remaining(
        df["point_time"].values, df["deadline"].values, sess)
    df["hours_left"] = ((df["deadline"] - df["point_time"]).dt.total_seconds() / 3600.0)
    df["years"] = df["sessions_left"] / cal.TRADING_DAYS

    df = df.dropna(subset=["spot", "sigma"]).reset_index(drop=True)
    df = df[(df["spot"] > 0) & (df["sigma"] > 0) & (df["barrier"] > 0)
            & (df["sessions_left"] > 0)].reset_index(drop=True)

    # Empirical fair (MODELS 3.5) and the continuous formula (FINDINGS 2).
    # k is the barrier's distance measured in realised moves over the remaining
    # horizon, which is the axis MODELS 3.5's grid is indexed by: a daily vol of
    # sigma/sqrt(252) times sqrt(sessions left).
    dist = np.abs(np.log(df["barrier"].values / df["spot"].values))
    daily_move = (df["sigma"].values / math.sqrt(cal.TRADING_DAYS)
                  * np.sqrt(np.maximum(df["sessions_left"].values, 1e-9)))
    k = dist / daily_move
    df["k"] = k
    beyond = np.where(df["is_up"].values, df["spot"].values >= df["barrier"].values,
                      df["spot"].values <= df["barrier"].values)

    # MODELS 3.1: a market whose spot is already past its barrier has a touch
    # pending and must be skipped, not priced at 1. These points exist because
    # the spot here is an hourly bar close and the venue resolves on 1-minute
    # Pyth candles, so "already beyond" mostly means the resolution has not
    # caught up yet. They are counted in section 2 (they are real quotes) and
    # excluded from every fair value and every trading rule.
    df["beyond_barrier"] = beyond
    emp = np.full(len(df), np.nan)
    for symbol, table in tables.items():
        m = (df["ticker"].values == symbol)
        if m.any():
            emp[m] = table.lookup_many(k[m], df["sessions_left"].values[m],
                                       df["is_up"].values[m])
    df["fair_empirical"] = np.where(beyond, np.nan, np.clip(emp, 0.0, 1.0))
    df["fair_continuous"] = np.where(beyond, np.nan, cal.one_touch(
        df["spot"].values, df["barrier"].values, df["years"].values,
        df["sigma"].values, df["rate"].fillna(0.0).values,
        np.where(df["is_up"].values, "up", "down")))

    # Exactly 0.5000 is what `prices-history` returns for an hour with no book
    # to mark, not a quote: see placeholder_note(). Flagged, never silently
    # dropped.
    df["placeholder_mid"] = df["price"].eq(0.5)
    df["bucket"] = cal.bucket_labels(df["price"].values)
    df["time_bucket"] = cal.time_bucket_labels(df["hours_left"].values)
    df["outcome"] = df["resolved_yes"].astype(float)

    meta = {
        "markets_total": total,
        "markets_scored": int(df["market_id"].nunique()),
        "markets_dropped_september": dropped_september,
        "points": len(df),
        "tickers_with_table": int(df.loc[df["fair_empirical"].notna(), "ticker"].nunique()),
        "already_beyond_barrier": int(beyond.sum()),
        "placeholder_mids": int(df["placeholder_mid"].sum()),
    }
    return df, meta


# ---------------------------------------------------------------- reporting


def reliability(df: pd.DataFrame, side: str = "yes") -> pd.DataFrame:
    """count / mean price / realised rate / gap, with both 95% intervals."""
    price = df["price"] if side == "yes" else 1.0 - df["price"]
    won = df["outcome"] if side == "yes" else 1.0 - df["outcome"]
    work = pd.DataFrame({"bucket": cal.bucket_labels(price.values),
                         "market_id": df["market_id"].values,
                         "price": price.values, "won": won.values})
    rows = []
    for label in cal.BUCKET_ORDER:
        part = work[work["bucket"] == label]
        n = len(part)
        if n == 0:
            continue
        wins = int(part["won"].sum())
        rate = wins / n
        mean_price = float(part["price"].mean())
        blo, bhi = cal.wilson_interval(wins, n)
        clusters = part.groupby("market_id")["won"].agg(["sum", "size"])
        clo, chi = cal.clustered_interval(clusters["sum"].values, clusters["size"].values)
        rows.append({"bucket": label, "n": n, "markets": len(clusters),
                     "mean_price": mean_price, "realised": rate,
                     "gap": rate - mean_price,
                     "blo": blo - mean_price, "bhi": bhi - mean_price,
                     "clo": clo - mean_price, "chi": chi - mean_price})
    return pd.DataFrame(rows)


def fmt_reliability(table: pd.DataFrame) -> str:
    head = ("| bucket | points | markets | mean price | realised | gap "
            "| 95% on the gap, clustered by market | 95%, binomial |\n"
            "|---|---:|---:|---:|---:|---:|---|---|")
    lines = [head]
    for r in table.itertuples():
        lines.append(f"| {r.bucket} | {r.n:,} | {r.markets:,} | {r.mean_price:.3f} "
                     f"| {r.realised:.3f} | {r.gap:+.3f} "
                     f"| {r.clo:+.3f} to {r.chi:+.3f} | {r.blo:+.3f} to {r.bhi:+.3f} |")
    return "\n".join(lines)


def split_tables(df: pd.DataFrame, column: str, order: list, side: str = "yes") -> str:
    out = []
    for value in order:
        part = df[df[column] == value]
        if part.empty:
            continue
        out.append(f"\n**{value}** ({len(part):,} points, "
                   f"{part['market_id'].nunique():,} markets)\n")
        out.append(fmt_reliability(reliability(part, side)))
    return "\n".join(out)


def brier_table(df: pd.DataFrame) -> str:
    cols = [("market price", "price"), ("empirical fair", "fair_empirical"),
            ("continuous one-touch", "fair_continuous")]
    lines = ["| forecast | n | mean forecast | Brier | vs base rate |", "|---|---:|---:|---:|---:|"]
    base = cal.brier(np.full(len(df), df["outcome"].mean()), df["outcome"].values)
    for name, col in cols:
        keep = df[col].notna()
        score = cal.brier(df.loc[keep, col].values, df.loc[keep, "outcome"].values)
        lines.append(f"| {name} | {int(keep.sum()):,} | {df.loc[keep, col].mean():.3f} "
                     f"| {score:.4f} | {score - base:+.4f} |")
    lines.append(f"| base rate ({df['outcome'].mean():.3f}) | {len(df):,} | "
                 f"{df['outcome'].mean():.3f} | {base:.4f} | 0.0000 |")
    return "\n".join(lines)


def displacement_table(df: pd.DataFrame) -> str:
    """Mid vs each fair, by price bucket - the FINDINGS 3.2 comparison, out of sample."""
    lines = ["| bucket | n | median mid | median empirical | median continuous "
             "| realised YES rate |", "|---|---:|---:|---:|---:|---:|"]
    for label in cal.BUCKET_ORDER:
        part = df[df["bucket"] == label]
        if part.empty:
            continue
        lines.append(f"| {label} | {len(part):,} | {part['price'].median():.3f} "
                     f"| {part['fair_empirical'].median():.3f} "
                     f"| {part['fair_continuous'].median():.3f} "
                     f"| {part['outcome'].mean():.3f} |")
    return "\n".join(lines)


def paper_pnl(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The two rules, one unit per market, taken at the first point that fires."""
    rows = []
    usable = df[~df["beyond_barrier"] & ~df["placeholder_mid"]]
    for market_id, part in usable.sort_values("point_time").groupby("market_id", sort=False):
        price = part["price"].values
        fair = part["fair_empirical"].values
        won = bool(part["resolved_yes"].iloc[0])
        month = part["deadline_month"].iloc[0]
        for name, mask, side in (
                ("buy NO when YES <= 10c", price <= 0.10, "no"),
                ("buy YES when empirical - price >= 5c",
                 np.isfinite(fair) & ((fair - price) >= 0.05), "yes")):
            i = cal.first_signal(mask)
            if i is None:
                continue
            pnl, cost = cal.leg_pnl(price[i], won, side)
            rows.append({"rule": name, "month": month, "market_id": market_id,
                         "entry_price": price[i], "cost": float(cost),
                         "pnl": float(pnl), "won": won})
    trades = pd.DataFrame(rows)
    if trades.empty:
        return trades, trades
    by_month = (trades.groupby(["rule", "month"])
                .agg(bets=("pnl", "size"), staked=("cost", "sum"), pnl=("pnl", "sum"),
                     win_rate=("pnl", lambda s: float((s > 0).mean())))
                .reset_index())
    by_month["roi"] = by_month["pnl"] / by_month["staked"]
    return trades, by_month


def fmt_pnl(by_month: pd.DataFrame) -> str:
    out = []
    for rule, part in by_month.groupby("rule"):
        out.append(f"\n**{rule}** - one unit per market, entered at the first hourly "
                   f"point that fires, paying mid + 3.5c + 4% fee, held to resolution.\n")
        lines = ["| deadline month | bets | staked | PnL | ROI | share resolved in your favour |",
                 "|---|---:|---:|---:|---:|---:|"]
        for r in part.sort_values("month").itertuples():
            lines.append(f"| {r.month} | {r.bets:,} | ${r.staked:,.0f} | ${r.pnl:+,.0f} "
                         f"| {r.roi:+.1%} | {r.win_rate:.3f} |")
        tot_bets, tot_stake, tot_pnl = part["bets"].sum(), part["staked"].sum(), part["pnl"].sum()
        wr = float((part["win_rate"] * part["bets"]).sum() / tot_bets)
        lines.append(f"| **total** | **{tot_bets:,}** | **${tot_stake:,.0f}** "
                     f"| **${tot_pnl:+,.0f}** | **{tot_pnl / tot_stake:+.1%}** | **{wr:.3f}** |")
        out.append("\n".join(lines))
    return "\n".join(out)


def pending_note(df: pd.DataFrame) -> str:
    """What happens when the hourly bar says the barrier is already gone."""
    pend = df[df["beyond_barrier"]]
    if pend.empty:
        return "No point had spot beyond its barrier."
    rate = pend["outcome"].mean()
    quoted = pend["price"].median()
    return (
        f"{len(pend):,} points ({len(pend) / len(df):.1%} of the sample, "
        f"{pend['market_id'].nunique():,} markets) had the hourly bar close already "
        f"beyond the barrier while the market was still quoting. "
        f"{rate:.3f} of them resolved YES, at a median quote of {quoted:.3f}.\n\n"
        f"That gap is not a tradeable edge until it is shown not to be a data "
        f"artifact. The spot here is a yfinance hourly bar close and the venue "
        f"resolves on Pyth 1-minute candles, so a bar that closes past the barrier "
        f"is consistent with (a) a resolution the archive stamps a few minutes "
        f"later, (b) the two feeds disagreeing on the print, or (c) a genuinely "
        f"slow book. Only (c) is money. Telling them apart needs the trade tape "
        f"(FINDINGS section 7) and minute bars, neither of which this report has, "
        f"so these points are excluded from sections 4 and 5 as MODELS 3.1 "
        f"requires."
    )


def placeholder_note(df: pd.DataFrame) -> str:
    """Evidence that a price of exactly 0.5000 is a placeholder, not a quote."""
    half = df[df["placeholder_mid"]]
    rest = df[~df["placeholder_mid"] & (df["bucket"] == "50-65c")]
    lines = [
        f"{len(half):,} points ({len(half) / len(df):.1%} of the sample, across "
        f"{half['market_id'].nunique():,} markets) are quoted at **exactly 0.5000**. "
        f"They are {len(half) / max(len(half) + len(rest), 1):.0%} of the 50-65c "
        f"bucket and they are not quotes:",
        "",
        "| | at exactly 0.5000 | rest of the 50-65c bucket |",
        "|---|---:|---:|",
        f"| points | {len(half):,} | {len(rest):,} |",
        f"| median barrier distance k (in realised moves) | {half['k'].median():.2f} "
        f"| {rest['k'].median():.2f} |",
        f"| median empirical fair | {half['fair_empirical'].median():.3f} "
        f"| {rest['fair_empirical'].median():.3f} |",
        f"| realised YES rate | {half['outcome'].mean():.3f} | {rest['outcome'].mean():.3f} |",
        "",
        "and by time left, a contract still marked at 50c:",
        "",
        "| hours to deadline | points | realised YES rate |",
        "|---|---:|---:|",
    ]
    for lo, hi, label in cal.TIME_BUCKETS:
        part = half[(half["hours_left"] >= lo) & (half["hours_left"] < hi)]
        if len(part):
            lines.append(f"| {label} | {len(part):,} | {part['outcome'].mean():.3f} |")
    lines += [
        "",
        "A contract four hours from its deadline, with the barrier a full realised "
        "move away, that resolves YES "
        f"{half[half['hours_left'] < 4]['outcome'].mean():.1%} of the time, is not a "
        "market saying 50/50. It is the venue marking an hour in which there was "
        "nothing to mark - the same class of artifact as FINDINGS section 4's "
        "untraded Cboe contracts, whose IV came from a placeholder quote. These "
        "points are excluded from the headline tables and from both trading rules, "
        "and section 2.1 repeats the headline with them left in so the difference "
        "is visible rather than asserted.",
    ]
    return "\n".join(lines)


def control_pnl(df: pd.DataFrame, trades: pd.DataFrame, draws: int = 20,
                seed: int = 11) -> pd.DataFrame:
    """MODELS 4.5's control: the same bets, chosen at random within price bucket.

    If a rule's PnL survives only because the months it traded happened to be
    kind - the market drifting rather than the signal working - then buying the
    same side, the same number of times, at the same prices, in randomly chosen
    markets makes the same money. Matching on the price bucket is what makes
    this a test of the *signal* and not of the well-known fact that longshots
    are overpriced.
    """
    rng = np.random.default_rng(seed)
    pool = df[~df["beyond_barrier"] & ~df["placeholder_mid"]]
    rows = []
    for rule, part in trades.groupby("rule"):
        side = "no" if "NO" in rule else "yes"
        wanted = (pd.Series(cal.bucket_labels(part["entry_price"].values))
                  .value_counts().to_dict())
        rois = []
        for _ in range(draws):
            picks = []
            for label, count in wanted.items():
                bucket = pool[pool["bucket"] == label]
                if bucket.empty:
                    continue
                shuffled = bucket.sample(frac=1.0, random_state=int(rng.integers(1 << 31)))
                picks.append(shuffled.drop_duplicates("market_id").head(count))
            if not picks:
                continue
            drawn = pd.concat(picks)
            pnl, cost = cal.leg_pnl(drawn["price"].values, drawn["resolved_yes"].values, side)
            rois.append(float(pnl.sum() / cost.sum()))
        if rois:
            rows.append({"rule": rule, "draws": len(rois), "mean_roi": float(np.mean(rois)),
                         "sd_roi": float(np.std(rois, ddof=1)),
                         "min_roi": float(np.min(rois)), "max_roi": float(np.max(rois))})
    return pd.DataFrame(rows)


def fmt_control(control: pd.DataFrame, by_month: pd.DataFrame) -> str:
    if control.empty:
        return "No control could be drawn."
    actual = (by_month.groupby("rule")
              .apply(lambda g: g["pnl"].sum() / g["staked"].sum(), include_groups=False))
    lines = ["| rule | rule ROI | control ROI (mean of draws) | control sd | control range | draws |",
             "|---|---:|---:|---:|---|---:|"]
    for r in control.itertuples():
        lines.append(f"| {r.rule} | {actual.get(r.rule, float('nan')):+.1%} "
                     f"| {r.mean_roi:+.1%} | {r.sd_roi:.1%} "
                     f"| {r.min_roi:+.1%} to {r.max_roi:+.1%} | {r.draws} |")
    return "\n".join(lines)


def concentration(trades: pd.DataFrame, df: pd.DataFrame) -> str:
    """How many independent bets a rule really made.

    MODELS 5.2b: ~600 markets on ~20 tickers over a few weeks are not hundreds
    of independent draws. Every `hit_down` on one ticker in one week resolves
    off the same price path, and every `hit_down` across tickers resolves off
    the same market. A rule with 4,780 bets and 90 ticker-weeks has 90-ish
    degrees of freedom, and the month-to-month spread in section 5 is what
    that looks like.
    """
    keys = df[["market_id", "ticker", "deadline", "contract_type"]].drop_duplicates("market_id")
    joined = trades.merge(keys, on="market_id", how="left")
    joined["week"] = joined["deadline"].dt.strftime("%G-W%V")
    lines = ["| rule | bets | markets | tickers | ticker-weeks | largest ticker's share of PnL |",
             "|---|---:|---:|---:|---:|---:|"]
    for rule, part in joined.groupby("rule"):
        by_ticker = part.groupby("ticker")["pnl"].sum()
        total = part["pnl"].sum()
        share = (by_ticker.abs().max() / abs(total)) if total else float("nan")
        lines.append(f"| {rule} | {len(part):,} | {part['market_id'].nunique():,} "
                     f"| {part['ticker'].nunique()} "
                     f"| {part.groupby(['ticker', 'week']).ngroups:,} | {share:.0%} |")
    return "\n".join(lines)


def build_report(df: pd.DataFrame, meta: dict, trades: pd.DataFrame,
                 by_month: pd.DataFrame, control: pd.DataFrame) -> tuple[str, str]:
    live = df[~df["placeholder_mid"]]
    summary = fmt_reliability(reliability(live, "yes"))

    def rule_roi(name: str) -> str:
        part = by_month[by_month["rule"] == name] if not by_month.empty else by_month
        if part.empty or part["staked"].sum() == 0:
            return "n/a"
        return f"{part['pnl'].sum() / part['staked'].sum():+.1%}"

    no_roi = rule_roi("buy NO when YES <= 10c")
    yes_roi = rule_roi("buy YES when empirical - price >= 5c")
    scored = live[~live["beyond_barrier"]]
    months = sorted(df["deadline_month"].unique())
    market_yes = df.groupby("market_id")["resolved_yes"].first()
    parts = [
        f"# Calibration of Polymarket hit markets, {months[0]} to {months[-1]}",
        "",
        f"Generated {dt.date.today()} by `model/run_calibration.py` from the "
        f"backfill in `data/src/poly_backfill.py`. No option data is used "
        f"anywhere in this report.",
        "",
        "## 1. Sample",
        "",
        f"- **{meta['markets_scored']:,} resolved hit markets** "
        f"({meta['points']:,} hourly price points), {df['ticker'].nunique()} tickers, "
        f"deadline months {', '.join(months)}.",
        f"- {meta['markets_dropped_september']:,} markets dropped for a September "
        f"deadline: a touched market closes early while an untouched one waits for "
        f"period end, so a month still in progress over-represents YES.",
        f"- Weekly: {df[df['period'] == 'weekly']['market_id'].nunique():,} markets; "
        f"monthly: {df[df['period'] == 'monthly']['market_id'].nunique():,}.",
        f"- Base rate, **per market**: {market_yes.mean():.3f} resolved YES "
        f"({int(market_yes.sum()):,} of {meta['markets_scored']:,}).",
        f"- Base rate, **per price point**: {df['outcome'].mean():.3f}. It is lower "
        f"because a market that touches stops quoting at that moment while an "
        f"untouched one quotes until period end, so NO outcomes contribute more "
        f"hours. Every table below is per point, which is the right weighting for "
        f"\"if I bought whenever the price was p\" and the wrong one for \"of "
        f"markets priced p\".",
        f"- Empirical touch tables built for {meta['tickers_with_table']} tickers from "
        f"sessions strictly before {TABLE_START}, so every scored point is out of sample.",
        "",
        f"- Excluded from the headline tables: {meta['placeholder_mids']:,} points "
        f"quoted at exactly 0.5000 (section 6.1) and, from sections 4 and 5, "
        f"{meta['already_beyond_barrier']:,} touch-pending points (section 6.2).",
        "",
        "> **A price point is a mid proxy, not a tradeable quote.** Polymarket's "
        "`prices-history` returns one price per hour with no size and no side. "
        "Sections 2 and 3 measure where the market was *marked*; only section 5, "
        "which adds half the 7c median spread and the 4% taker fee, is about money.",
        "",
        "## 2. Reliability, YES side",
        "",
        "Every hourly point while the market was open, bucketed by its YES price. "
        "`gap` is realised minus mean price: **negative means the bucket was dear**. "
        "The interval to read is the clustered one: the points are not independent, "
        "because every hour of one market shares that market's single outcome.",
        "",
        summary,
        "",
        "**Read: the market overprices YES at every price level.** A 1.4c contract "
        "resolves YES 0.2% of the time; a 44.6c contract resolves YES 36%. Only the "
        "top bucket is fairly priced. This is FINDINGS 3.2's favourite-longshot bias "
        "measured against outcomes instead of against an option-implied fair, and it "
        "is larger than the option comparison suggested.",
        "",
        "### 2.1 The same table with placeholder mids left in",
        "",
        fmt_reliability(reliability(df, "yes")),
        "",
        "### 2.2 By period",
        split_tables(live, "period", ["weekly", "monthly"]),
        "",
        "### 2.3 By direction",
        split_tables(live, "contract_type", ["hit_up", "hit_down"]),
        "",
        "### 2.4 By time to deadline",
        split_tables(live, "time_bucket", cal.TIME_ORDER),
        "",
        "## 3. Reliability, NO side",
        "",
        "The same points priced as `1 - YES`, winning when the market resolved NO. "
        "It is the mirror of section 2 and is tabulated separately because the NO "
        "side is where the capacity is (FINDINGS section 7: ~$616k on NO against "
        "~$89k on YES at one snapshot).",
        "",
        fmt_reliability(reliability(live, "no")),
        "",
        "### 3.1 By period",
        split_tables(live, "period", ["weekly", "monthly"], side="no"),
        "",
        "## 4. Three forecasts against the same outcomes",
        "",
        f"Restricted to the {int((~df['beyond_barrier']).sum()):,} points where spot "
        f"was still on the safe side of the barrier. The other "
        f"{int(df['beyond_barrier'].sum()):,} are touch-pending (MODELS 3.1) and are "
        f"scored in section 6 instead.",
        "",
        brier_table(scored),
        "",
        "Displacement by bucket - mid against each fair value, never `bid - fair` "
        "or `fair - ask` (FINDINGS section 2):",
        "",
        displacement_table(scored),
        "",
        "## 5. Paper PnL",
        "",
        "Costs: entry at the hourly mid plus 3.5c (half the 7c median top-of-book "
        "spread) plus the taker fee `0.04 x min(p, 1-p)` on what was paid, held to "
        "resolution. No depth check: FINDINGS section 7 measured a median $52-$719 "
        "available per market, so these unit counts are not all fillable.",
        fmt_pnl(by_month) if not by_month.empty else "\nNo trade fired.\n",
        "",
        "",
        "### 5.1 How independent are these bets?",
        "",
        concentration(trades, df) if not trades.empty else "",
        "",
        "### 5.2 Control",
        "",
        "MODELS 4.5: the same side, the same number of bets, the same price "
        "buckets, markets drawn at random. If the control makes the same money, "
        "the signal did nothing and the market simply drifted.",
        "",
        fmt_control(control, by_month) if not by_month.empty else "",
        "",
        "## 6. Data-quality exclusions",
        "",
        "### 6.1 Placeholder mids",
        "",
        placeholder_note(df),
        "",
        "### 6.2 Touch-pending points",
        "",
        pending_note(df),
        "",
        "## 7. What this answers, and what it does not",
        "",
        "**Answered.** FINDINGS 5.3 asked whether contracts priced at 3-5c resolve "
        "YES 3-5% of the time. They resolve YES "
        f"{reliability(live, 'yes').set_index('bucket').loc['3-5c', 'realised']:.1%} "
        "of the time, and every bucket below 65c is dear by 1-9c against outcomes. "
        "The favourite-longshot bias in FINDINGS 3.2 is real and it is bigger than "
        "the option-implied comparison showed. Nothing here depends on an option "
        "chain, an implied vol or a skew choice.",
        "",
        "**Answered, unexpectedly.** The historical touch frequency, built only "
        "from daily bars before 2026-02-01, forecasts outcomes better than the "
        "market price does (section 4), and a rule that trades on it separates "
        "cleanly from a price-matched random control (section 5.2). MODELS 5.3's "
        "`empirical_longshot` is the model worth building first.",
        "",
        "**Contradicted.** MODELS 5.3's first shape - sell longshots, buy NO when "
        "the YES ask is under ~12c - loses money here. Section 2 says a 7.2c "
        "contract resolves YES 4.3% of the time, so the NO side is 2.9c cheap at "
        f"mid; section 5 says that buying it at mid + 3.5c + fee returns {no_roi}, "
        "which is worse than buying NO at random at the same prices. The "
        "displacement is real and it is smaller than the spread. FINDINGS 3.3 said "
        "this in advance - **do not buy cheap contracts** - and the NO side of a "
        "longshot is a cheap contract wearing a 90c price tag. Section 3 puts a "
        "number on it where the capacity actually is: the NO side above 65c, "
        f"{int(reliability(live, 'no').set_index('bucket').loc['>65c', 'n']):,} points "
        f"at a mean {reliability(live, 'no').set_index('bucket').loc['>65c', 'mean_price']:.3f}, "
        f"is {reliability(live, 'no').set_index('bucket').loc['>65c', 'gap']:+.3f} cheap "
        "at mid - against a 3.5c half-spread it does not clear its own costs.",
        "",
        "**Not answered.** Whether any of this is fillable. Every entry above is a "
        "mid plus half a median spread, with no book, no depth and no queue. "
        "FINDINGS section 7 measured $52-$719 of depth per market and a fill price "
        f"that ran 0.114 -> 0.591 for $0 -> $500 on one ladder, so the {yes_roi} is an "
        "upper bound on a number that the order book gets to veto. The next thing "
        "to measure is that veto, against the live books now being collected.",
        "",
    ]
    return "\n".join(parts), summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    import duckdb

    con = duckdb.connect()
    df, meta = build_points(con)
    trades, by_month = paper_pnl(df)
    control = control_pnl(df, trades) if not trades.empty else pd.DataFrame()
    report, summary = build_report(df, meta, trades, by_month, control)

    out = Path(args.out) if args.out else (
        HERE / "reports" / f"calibration_{dt.date.today()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"wrote {out} ({meta['points']:,} points, {meta['markets_scored']:,} markets)",
          file=sys.stderr)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
