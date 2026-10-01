"""Explicit Arrow schemas per table.

Type inference is not safe here: the venues return numbers as strings
inconsistently (tick_size arrives as "0.01" or 0.01, best_bid as "" or 0.5), and
a column whose type differs between partitions makes the whole DuckDB glob
unreadable. Declaring the schema once keeps every partition union-compatible.
"""
from __future__ import annotations

import pyarrow as pa

S = pa.string()
F = pa.float64()
I = pa.int64()
B = pa.bool_()

_COMMON = [("cycle_id", S), ("fetched_at_utc", S)]


def _schema(fields) -> pa.Schema:
    return pa.schema([pa.field(n, t) for n, t in _COMMON + list(fields)])


SCHEMAS: dict[str, pa.Schema] = {
    "poly_markets": _schema([
        ("event_id", S), ("event_slug", S), ("event_title", S), ("event_end_date", S),
        ("market_id", S), ("market_slug", S), ("condition_id", S), ("question", S),
        ("group_item_title", S), ("ticker", S), ("contract_type", S),
        ("strike", F), ("barrier", F), ("range_low", F), ("range_high", F),
        ("direction", S), ("period", S), ("parse_reason", S), ("end_date", S),
        ("outcomes", S), ("token_id_0", S), ("token_id_1", S),
        ("outcome_0", S), ("outcome_1", S),
        ("tick_size", F), ("min_order_size", F),
        ("maker_base_fee", F), ("taker_base_fee", F), ("fee_type", S),
        ("fees_enabled", B), ("fee_schedule", S), ("neg_risk", B),
        ("accepting_orders", B), ("active", B), ("closed", B),
        ("volume", F), ("liquidity", F), ("last_trade_price", F),
        ("best_bid", F), ("best_ask", F), ("spread", F),
        ("event_tags", S), ("rules_text", S), ("resolution_source", S),
        ("umaResolutionStatus", S),
        ("created_at", S), ("start_date", S), ("closed_time", S),
        ("outcome_prices", S), ("result", S),
    ]),
    "poly_books": _schema([
        ("book_timestamp_utc", S), ("book_hash", S), ("token_id", S),
        ("condition_id", S), ("market_id", S), ("event_slug", S), ("ticker", S),
        ("contract_type", S), ("strike", F), ("barrier", F),
        ("end_date", S), ("period", S),
        ("outcome", S), ("outcome_index", I),
        ("tick_size", F), ("min_order_size", F), ("neg_risk", B),
        ("book_last_trade_price", F),
        ("side", S), ("level_rank", I), ("depth_rank", I), ("price", F), ("size", F),
    ]),
    "poly_price_history": _schema([
        ("token_id", S), ("market_id", S), ("ticker", S), ("outcome_index", I),
        ("point_time_utc", S), ("price", F), ("interval", S), ("fidelity", I),
    ]),
    "poly_unparsed": _schema([
        ("market_id", S), ("question", S), ("event_title", S),
        ("group_item_title", S), ("reason", S),
    ]),
    "kalshi_markets": _schema([
        ("series_ticker", S), ("event_ticker", S), ("ticker", S), ("title", S),
        ("subtitle", S), ("yes_sub_title", S), ("no_sub_title", S),
        ("market_type", S), ("strike_type", S), ("floor_strike", F),
        ("cap_strike", F), ("custom_strike", S), ("status", S),
        ("open_time", S), ("close_time", S), ("expiration_time", S),
        ("expected_expiration_time", S),
        ("yes_bid", F), ("yes_ask", F), ("no_bid", F), ("no_ask", F),
        ("yes_bid_size", F), ("yes_ask_size", F),
        ("last_price", F), ("previous_price", F),
        ("volume", F), ("volume_24h", F), ("open_interest", F),
        ("liquidity", F), ("notional_value", F),
        ("result", S), ("can_close_early", B),
        ("rules_primary", S), ("rules_secondary", S),
        ("settlement_sources", S),
        # Raw values exactly as returned, before normalisation to probabilities.
        ("raw_yes_bid_dollars", S), ("raw_yes_ask_dollars", S),
        ("raw_last_price_dollars", S), ("raw_volume_fp", S),
        ("raw_open_interest_fp", S),
    ]),
    "kalshi_books": _schema([
        ("series_ticker", S), ("event_ticker", S), ("ticker", S),
        ("side", S), ("level_rank", I), ("depth_rank", I),
        ("price_prob", F), ("size", F),
        ("raw_price", S), ("raw_size", S), ("price_units", S),
    ]),
    "option_quotes": _schema([
        ("source", S), ("symbol", S), ("underlying", S),
        ("contract_symbol", S), ("option_type", S), ("expiry", S), ("strike", F),
        ("bid", F), ("ask", F), ("bid_size", F), ("ask_size", F),
        ("last_price", F), ("last_trade_time_utc", S),
        ("volume", F), ("open_interest", F),
        ("source_iv", F), ("source_delta", F), ("source_gamma", F),
        ("source_theta", F), ("source_vega", F), ("source_rho", F),
        ("source_theo", F),
        ("source_quote_time_utc", S),
        ("open", F), ("high", F), ("low", F), ("prev_day_close", F),
        ("in_the_money", B),
    ]),
    "underlying_quotes": _schema([
        ("source", S), ("symbol", S), ("last_price", F),
        ("bid", F), ("ask", F), ("bid_size", F), ("ask_size", F),
        ("open", F), ("high", F), ("low", F), ("prev_close", F),
        ("volume", F), ("iv30", F),
        ("source_quote_time_utc", S),
    ]),
    "underlying_bars": _schema([
        ("source", S), ("symbol", S), ("interval", S),
        ("bar_time_utc", S), ("bar_time_market", S),
        ("open", F), ("high", F), ("low", F), ("close", F),
        ("volume", F), ("dividends", F), ("stock_splits", F),
    ]),
    "rates": _schema([
        ("source", S), ("series", S), ("obs_date", S),
        ("tenor_label", S), ("tenor_years", F), ("rate_percent", F),
    ]),
    "dividends": _schema([
        ("source", S), ("symbol", S), ("kind", S),
        ("ex_date", S), ("pay_date", S), ("amount", F), ("dividend_yield", F),
    ]),
    "earnings": _schema([
        ("source", S), ("symbol", S), ("kind", S), ("earnings_date_utc", S),
        ("eps_estimate", F), ("eps_actual", F), ("surprise_percent", F),
    ]),
    "resolutions": _schema([
        ("venue", S), ("market_id", S), ("event_slug", S), ("ticker", S),
        ("contract_type", S), ("strike", F), ("barrier", F), ("question", S),
        ("uma_status", S), ("end_date", S), ("result", S), ("settled_at_utc", S),
        ("outcome_prices", S), ("created_at", S),
    ]),

    "runs": _schema([
        ("job", S), ("started_at_utc", S), ("ended_at_utc", S),
        ("duration_seconds", F), ("status", S),
        ("counts_json", S), ("failure_count", I), ("notes", S),
    ]),
    "failures": _schema([
        ("venue", S), ("identifier", S), ("url", S), ("error", S),
        ("failed_at_utc", S),
    ]),
}


def coerce(table: str, rows: list[dict]) -> tuple[list[dict], pa.Schema | None]:
    """Cast rows to the declared schema. Unknown tables fall back to inference."""
    schema = SCHEMAS.get(table)
    if schema is None:
        return rows, None
    out = []
    for row in rows:
        clean = {}
        for field in schema:
            clean[field.name] = _cast(row.get(field.name), field.type)
        out.append(clean)
    return out, schema


def _cast(value, arrow_type):
    import json as _json

    if value is None:
        return None
    if arrow_type == S:
        if isinstance(value, (dict, list, tuple)):
            return _json.dumps(value)
        text = str(value)
        return text if text != "" else None
    if arrow_type == F:
        if isinstance(value, bool):
            return float(value)
        try:
            return float(str(value).replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError):
            return None
    if arrow_type == I:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if arrow_type == B:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1", "yes"):
            return True
        if text in ("false", "0", "no"):
            return False
        return None
    return value
