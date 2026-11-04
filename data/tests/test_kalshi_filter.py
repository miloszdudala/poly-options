"""Which Kalshi markets earn a depth request.

Depth costs one request each and the ladder series are mostly filler: on
2026-09-16, 2,738 of the 2,800 open KXNASDAQ100U markets had never traded and
~1,000 were quoted more than 20c wide. Top of book for every market is stored
regardless, from GET /markets - this filter only gates the extra request.
"""
import kalshi


def mkt(**kw):
    base = {"series_ticker": "KXNASDAQ100U", "ticker": "T", "open_interest": 0,
            "volume": 0, "yes_bid": 0.10, "yes_ask": 0.90}
    return {**base, **kw}


def test_untraded_and_wide_is_skipped():
    assert kalshi.wants_book(mkt()) is False


def test_tight_quote_is_kept_even_if_never_traded():
    """A liftable quote is tradeable whether or not anyone has lifted it yet."""
    assert kalshi.wants_book(mkt(yes_bid=0.48, yes_ask=0.52)) is True


def test_open_interest_keeps_a_wide_market():
    assert kalshi.wants_book(mkt(open_interest=5)) is True
    assert kalshi.wants_book(mkt(volume=5)) is True


def test_missing_quote_needs_a_trade_to_qualify():
    assert kalshi.wants_book(mkt(yes_bid=None, yes_ask=None)) is False
    assert kalshi.wants_book(mkt(yes_bid=None, yes_ask=None, open_interest=1)) is True


def test_always_series_bypasses_the_filter():
    """The year-end series are the tight ones; never let the filter drop them."""
    assert kalshi.wants_book(mkt(series_ticker="KXNASDAQ100Y")) is True


def test_boundary_is_inclusive():
    assert kalshi.wants_book(mkt(yes_bid=0.40, yes_ask=0.60)) is True   # exactly 0.20
    assert kalshi.wants_book(mkt(yes_bid=0.40, yes_ask=0.61)) is False


def test_settled_markets_are_stored_with_kalshis_settlement_time(monkeypatch):
    pages = [{"markets": [{"ticker": "KXINX-T", "result": "no", "expiration_value": "7568.92",
                           "close_time": "2026-11-03T21:00:00Z",
                           "settlement_ts": "2026-11-04T00:02:10Z"}], "cursor": "next"},
             {"markets": [], "cursor": ""}]

    class FakeHttp:
        def get(self, url, venue, identifier, params):
            assert params["status"] == "settled"
            return pages.pop(0) if pages else {"markets": []}

    written = {}
    monkeypatch.setattr(kalshi, "all_series", lambda: ["KXINX"])
    monkeypatch.setattr(kalshi, "write_table",
                        lambda table, rows, partition_date: written.update({table: rows}))
    assert kalshi.collect_resolutions(FakeHttp(), "cycle") == 1
    row = written["kalshi_resolutions"][0]
    assert (row["ticker"], row["result"], row["expiration_value"]) == ("KXINX-T", "no", 7568.92)
    assert row["settled_at_utc"] == "2026-11-04T00:02:10Z"
