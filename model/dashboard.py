"""The dashboard: one local HTML file built from the ledgers, no server.

    .venv/bin/python model/dashboard.py          # rebuild model/reports/dashboard.html and open it
    .venv/bin/python model/dashboard.py --no-open

Rebuilt every time it is opened this way, from whatever `bets` ledgers
`run_all.py`, `daily.py` or `run.py --save` last wrote. Everything the page
needs is inside the file: the bets as JSON and a small script that filters
and totals them, so it opens with no network.

Shows, for the filters chosen (model, type, entry dates):
* one row per model x type: bets, money in, EV promised at entry, realised
  PnL, realised / promised, and settled periods;
* per type: PnL by week (by month for monthly types), promised EV beside it;
* every bet, behind its row.
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "store" / "ledger"
OUT = HERE / "reports" / "dashboard.html"

COLUMNS = ["model", "type", "account", "market_id", "side", "ticker", "contract_type",
           "level", "deadline_utc", "period", "entry_at_utc", "win_prob", "shares",
           "cost_usd", "ev_usd", "result", "payout_usd", "pnl_usd", "settled_at_utc"]


def load_bets(ledger: Path = LEDGER) -> pd.DataFrame:
    """Every `bets` ledger under `ledger`, one row per bet. The run's folder
    name is kept as `run`, so two accounts or tags of one model x type stay
    apart."""
    parts = sorted(ledger.glob("*/bets/*.parquet")) if ledger.exists() else []
    if not parts:
        return pd.DataFrame(columns=COLUMNS + ["run"])
    frames = [pd.read_parquet(p).assign(run=p.parent.parent.name) for p in parts]
    return pd.concat(frames, ignore_index=True)


def records(bets: pd.DataFrame) -> list[dict]:
    """The bets as plain JSON records: numbers stay numbers, missing is null."""
    keep = bets[[c for c in COLUMNS + ["run"] if c in bets.columns]]
    keep = keep.astype(object).where(keep.notna(), None)
    return keep.to_dict("records")


def render(bets: pd.DataFrame) -> str:
    payload = json.dumps(records(bets), default=str).replace("</", "<\\/")
    return PAGE.replace("/*DATA*/[]", payload)


def build(ledger: Path = LEDGER, out: Path = OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(load_bets(ledger)))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-open", action="store_true", help="build without opening")
    args = parser.parse_args(argv)
    path = build()
    print(f"wrote {path}", file=sys.stderr)
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Poly-options dashboard</title>
<style>
  :root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#e3e3e8; --head:#f5f5f7;
          --pos:#127a3a; --neg:#b3261e; --hi:#eef4ff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161618; --fg:#ececf0; --muted:#9a9aa2; --line:#2e2e33; --head:#1f1f23;
            --pos:#4cc47a; --neg:#ff7b72; --hi:#1d2a40; } }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
         font:14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:24px 0 8px; }
  .muted { color:var(--muted); }
  .filters { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin:12px 0; }
  label { display:flex; flex-direction:column; font-size:12px; color:var(--muted); gap:2px; }
  select, input { font:inherit; padding:4px 6px; background:var(--bg); color:var(--fg);
                  border:1px solid var(--line); border-radius:6px; }
  .wrap { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
  th, td { padding:6px 8px; border-bottom:1px solid var(--line); text-align:right;
           white-space:nowrap; }
  th { background:var(--head); font-weight:600; position:sticky; top:0; }
  th:first-child, td:first-child, .l { text-align:left; }
  tr.row { cursor:pointer; } tr.row:hover, tr.open { background:var(--hi); }
  .pos { color:var(--pos); } .neg { color:var(--neg); }
  .bets td { font-size:12px; }
</style>
</head>
<body>
<h1>Promised against realised</h1>
<div class="muted" id="summary-line"></div>
<div class="filters">
  <label>Model <select id="f-model"></select></label>
  <label>Type <select id="f-type"></select></label>
  <label>Entered from <input type="date" id="f-from"></label>
  <label>to <input type="date" id="f-to"></label>
</div>

<h2>By model and type</h2>
<p class="muted">Click a row for its bets. Promised EV and PnL are compared on the
same settled bets; <em>promised, all</em> includes the open ones.</p>
<div class="wrap"><table id="t-runs"></table></div>

<h2>By period of entry</h2>
<p class="muted">Week from Monday, or month for monthly types (New York time).</p>
<div id="periods"></div>

<script>
const BETS = /*DATA*/[];
const $ = (id) => document.getElementById(id);
const usd = (x) => x == null || !isFinite(x) ? "-" :
  (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString("en-US", {minimumFractionDigits: 2,
                                                              maximumFractionDigits: 2});
const cls = (x) => x > 0 ? "pos" : x < 0 ? "neg" : "";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

function nyDate(iso) {
  const d = new Date(iso);
  return new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York", year: "numeric",
                                           month: "2-digit", day: "2-digit"}).format(d);
}
function periodOf(bet, monthly) {
  const day = nyDate(bet.entry_at_utc);
  if (monthly) return day.slice(0, 7);
  const d = new Date(day + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}
const isMonthly = (type) => /monthly/.test(type);
const label = (b) => b.run || (b.model + "__" + b.type);

function options(select, values) {
  select.innerHTML = '<option value="">all</option>' +
    values.map((v) => `<option>${esc(v)}</option>`).join("");
}

function filtered() {
  const m = $("f-model").value, t = $("f-type").value;
  const from = $("f-from").value, to = $("f-to").value;
  return BETS.filter((b) => {
    const day = nyDate(b.entry_at_utc);
    return (!m || b.model === m) && (!t || b.type === t) &&
           (!from || day >= from) && (!to || day <= to);
  });
}

function totals(bets, monthly) {
  const settled = bets.filter((b) => b.pnl_usd != null);
  const sum = (xs, k) => xs.reduce((a, b) => a + (b[k] || 0), 0);
  const promisedSettled = sum(settled, "ev_usd"), pnl = sum(settled, "pnl_usd");
  const periods = {};
  for (const b of bets) {
    const p = periodOf(b, monthly);
    periods[p] = periods[p] === undefined ? b.pnl_usd != null : periods[p] && b.pnl_usd != null;
  }
  return {bets: bets.length, money: sum(bets, "cost_usd"), promised: sum(bets, "ev_usd"),
          settled: settled.length, promisedSettled, pnl,
          ratio: promisedSettled ? pnl / promisedSettled : null,
          periodsSettled: Object.values(periods).filter(Boolean).length};
}

function group(bets, key) {
  const out = new Map();
  for (const b of bets) {
    const k = key(b);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(b);
  }
  return [...out.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1);
}

function betRows(bets) {
  const head = ["entered (NY)", "ticker", "contract", "level", "side", "win prob", "shares",
                "cost", "promised EV", "result", "PnL", "deadline"];
  const rows = bets.slice().sort((a, b) => a.entry_at_utc < b.entry_at_utc ? -1 : 1).map((b) =>
    `<tr><td>${nyDate(b.entry_at_utc)}</td><td class="l">${esc(b.ticker)}</td>` +
    `<td class="l">${esc(b.contract_type)}</td><td>${b.level ?? "-"}</td>` +
    `<td class="l">${esc(b.side)}</td><td>${b.win_prob == null ? "-" : b.win_prob.toFixed(3)}</td>` +
    `<td>${(b.shares || 0).toFixed(1)}</td><td>${usd(b.cost_usd)}</td><td>${usd(b.ev_usd)}</td>` +
    `<td class="l">${esc(b.result ?? "open")}</td>` +
    `<td class="${cls(b.pnl_usd)}">${usd(b.pnl_usd)}</td>` +
    `<td>${b.deadline_utc ? nyDate(b.deadline_utc) : "-"}</td></tr>`).join("");
  return `<table class="bets"><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows}</table>`;
}

function draw() {
  const bets = filtered();
  const all = totals(bets, false);
  $("summary-line").textContent = `${all.bets.toLocaleString()} bets, ${usd(all.money)} in; ` +
    `${all.settled.toLocaleString()} settled: PnL ${usd(all.pnl)} against ` +
    `${usd(all.promisedSettled)} promised.`;

  const head = ["model", "type", "bets", "money in", "promised, all", "settled",
                "promised (settled)", "PnL (settled)", "realised / promised", "settled periods"];
  let html = `<tr>${head.map((h, i) => `<th${i < 2 ? ' class="l"' : ""}>${h}</th>`).join("")}</tr>`;
  for (const [run, mine] of group(bets, label)) {
    const t = totals(mine, isMonthly(mine[0].type));
    html += `<tr class="row" data-run="${esc(run)}"><td>${esc(mine[0].model)}</td>` +
      `<td class="l">${esc(mine[0].type)}${run !== mine[0].model + "__" + mine[0].type ?
        ` <span class="muted">(${esc(run)})</span>` : ""}</td>` +
      `<td>${t.bets}</td><td>${usd(t.money)}</td><td>${usd(t.promised)}</td>` +
      `<td>${t.settled}</td><td>${usd(t.promisedSettled)}</td>` +
      `<td class="${cls(t.pnl)}">${usd(t.pnl)}</td>` +
      `<td>${t.ratio == null ? "-" : t.ratio.toFixed(2)}</td><td>${t.periodsSettled}</td></tr>` +
      `<tr class="detail" data-for="${esc(run)}" hidden><td colspan="10"><div class="wrap">` +
      `${betRows(mine)}</div></td></tr>`;
  }
  if (!bets.length) html += '<tr><td colspan="10" class="l muted">No bets match.</td></tr>';
  $("t-runs").innerHTML = html;
  for (const row of document.querySelectorAll("tr.row")) {
    row.onclick = () => {
      const detail = row.nextElementSibling;
      detail.hidden = !detail.hidden;
      row.classList.toggle("open", !detail.hidden);
    };
  }

  let periods = "";
  for (const [type, mine] of group(bets, (b) => b.type)) {
    const monthly = isMonthly(type);
    periods += `<h3>${esc(type)}</h3><div class="wrap"><table><tr><th>${monthly ? "month" : "week"}</th>` +
      "<th>bets</th><th>money in</th><th>promised, all</th><th>settled</th>" +
      "<th>promised (settled)</th><th>PnL (settled)</th></tr>";
    for (const [p, inPeriod] of group(mine, (b) => periodOf(b, monthly))) {
      const t = totals(inPeriod, monthly);
      periods += `<tr><td>${p}</td><td>${t.bets}</td><td>${usd(t.money)}</td>` +
        `<td>${usd(t.promised)}</td><td>${t.settled}</td><td>${usd(t.promisedSettled)}</td>` +
        `<td class="${cls(t.pnl)}">${usd(t.pnl)}</td></tr>`;
    }
    periods += "</table></div>";
  }
  $("periods").innerHTML = periods || '<p class="muted">No bets match.</p>';
}

options($("f-model"), [...new Set(BETS.map((b) => b.model))].sort());
options($("f-type"), [...new Set(BETS.map((b) => b.type))].sort());
for (const id of ["f-model", "f-type", "f-from", "f-to"]) $(id).onchange = draw;
draw();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
