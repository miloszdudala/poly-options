"""The chain types price the close, and never on a quote newer than the book."""
import pandas as pd

import run

run.discover_types()                 # puts model/types on sys.path
import _chain  # noqa: E402


def calls(*rows):
    return pd.DataFrame([{"contract_symbol": s, "strike": k, "mid": m} for s, k, m in rows])


def test_morning_settled_index_options_are_dropped_on_a_third_friday():
    chain = calls(("SPX261016C05000000", 5000.0, 90.0), ("SPXW261016C05000000", 5000.0, 60.0),
                  ("NDX261016C20000000", 20000.0, 90.0), ("NDXP261016C20000000", 20000.0, 60.0))
    assert list(_chain.settled_at_the_close(chain)["contract_symbol"]) == [
        "SPXW261016C05000000", "NDXP261016C20000000"]


def test_a_third_friday_prices_off_the_close_settled_options_alone():
    chain = calls(("SPX261016C05000000", 5000.0, 99.0), ("SPX261016C05010000", 5010.0, 90.0),
                  ("SPXW261016C05000000", 5000.0, 60.0), ("SPXW261016C05010000", 5010.0, 55.0))
    chain["option_type"] = "call"
    puts = chain.assign(option_type="put", mid=[99.0, 90.0, 60.0, 55.0])
    chain = pd.concat([chain, puts], ignore_index=True)
    p = _chain.prob_above(_chain.settled_at_the_close(chain), 5005.0, 0.0, 0.01)
    assert abs(p - 0.5) < 1e-9


def test_a_stock_whose_root_is_its_ticker_keeps_its_options():
    chain = calls(("NVDA261016C00100000", 100.0, 5.0))
    assert len(_chain.settled_at_the_close(chain)) == 1


def test_a_chain_type_skips_a_quote_newer_than_the_book(monkeypatch):
    import data
    bet_type = run.discover_types()["poly-close-above-weekly-stock"]()
    fetched = pd.Timestamp("2026-10-14T15:00:00Z")
    deadline = pd.Timestamp("2026-10-16T20:00:00Z")
    monkeypatch.setattr(bet_type, "books", lambda cycle_id: pd.DataFrame([{
        "market_id": "m", "ticker": "NVDA", "end_date": deadline, "period": "weekly",
        "fetched_at_utc": fetched}]))
    monkeypatch.setattr(bet_type, "terms", lambda: pd.DataFrame(
        [{"strike": 105.0, "direction": "above"}], index=pd.Index(["m"], name="market_id")))
    monkeypatch.setattr(data, "option_chain", lambda cycle_id, symbols: pd.DataFrame([
        {"underlying": "NVDA", "option_type": "call", "contract_symbol": f"NVDA261016C{k}",
         "expiry": pd.Timestamp("2026-10-16"), "strike": float(k), "mid": m}
        for k, m in ((100, 6.0), (110, 2.0))]))
    monkeypatch.setattr(data, "quote_times", lambda cycle_id: pd.Series(
        {"NVDA": fetched + pd.Timedelta(minutes=5)}))
    monkeypatch.setattr(data, "risk_free_rate", lambda when: 0.0)
    assert bet_type.priced("c").empty
    assert bet_type.skipped == {_chain.SKIP_QUOTE_AFTER_BOOK: 1}
