"""Risk-free rates from Treasury.gov and FRED. Both are free and keyless."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re

from config import markets_cfg
from storage import write_raw, write_table
from util import Http, get_logger, utc_iso

log = get_logger("reference")

# "4 WEEKS COUPON EQUIVALENT" -> 4/52 years; "10 YR" -> 10 years.
_TENOR_RE = re.compile(r"(\d+)\s*(WEEKS?|MO|MONTHS?|YR|YEARS?)", re.I)


def tenor_years(label: str) -> float | None:
    m = _TENOR_RE.search(label or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).upper()
    if unit.startswith("WEEK"):
        return n / 52.0
    if unit.startswith("MO"):
        return n / 12.0
    return float(n)


def _parse_treasury_csv(text: str, source: str, series: str,
                        cycle_id: str, fetched_at: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for rec in reader:
        raw_date = (rec.get("Date") or "").strip()
        if not raw_date:
            continue
        try:
            obs = dt.datetime.strptime(raw_date, "%m/%d/%Y").date()
        except ValueError:
            continue
        for label, value in rec.items():
            if label == "Date" or value in (None, "", "N/A"):
                continue
            try:
                rate = float(value)
            except ValueError:
                continue
            rows.append({
                "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                "source": source, "series": series,
                "obs_date": obs.isoformat(),
                "tenor_label": label, "tenor_years": tenor_years(label),
                "rate_percent": rate,
            })
    return rows


def collect_rates(http: Http, cycle_id: str, year: int | None = None) -> dict:
    """Treasury bill + par yield curves, with FRED as a cross-check."""
    cfg = markets_cfg()["rates"]
    fetched_at = utc_iso()
    year = year or dt.date.today().year
    rows: list[dict] = []

    for series, url_tpl in (("bill_rates", cfg["treasury_bill_csv"]),
                            ("par_yield_curve", cfg["treasury_par_csv"])):
        url = url_tpl.format(year=year)
        body = http.get(url, venue="treasury", identifier=series, expect_json=False)
        if not body:
            continue
        text = body.decode("utf-8-sig", errors="replace")
        write_raw("treasury", series, text, cycle_id)
        rows += _parse_treasury_csv(text, "treasury.gov", series, cycle_id, fetched_at)

    for series in cfg["fred_series"]:
        url = cfg["fred_csv"].format(series=series)
        body = http.get(url, venue="fred", identifier=series, expect_json=False)
        if not body:
            continue
        text = body.decode("utf-8-sig", errors="replace")
        write_raw("fred", series, text, cycle_id)
        reader = csv.DictReader(io.StringIO(text))
        for rec in reader:
            keys = list(rec)
            date_key = keys[0]
            value = rec.get(series) or rec.get(keys[-1])
            if value in (None, "", "."):
                continue
            try:
                rate = float(value)
            except ValueError:
                continue
            rows.append({
                "cycle_id": cycle_id, "fetched_at_utc": fetched_at,
                "source": "fred", "series": series,
                "obs_date": (rec.get(date_key) or "").strip(),
                "tenor_label": series, "tenor_years": _FRED_TENORS.get(series),
                "rate_percent": rate,
            })

    if rows:
        write_table("rates", rows)
    return {"rates": len(rows)}


_FRED_TENORS = {"DGS1MO": 1 / 12, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1.0}
