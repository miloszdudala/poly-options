"""Order-book flattening.

The CLOB returns both sides worst-price-first, which is the opposite of what a
reader expects, so depth_rank must be normalised to best-first on both sides.
"""
from poly_books import _levels

# Shape and ordering taken verbatim from a live GET /book response.
REAL_BOOK = {
    "market": "0x5375",
    "asset_id": "54108274",
    "timestamp": "1789532629085",
    "hash": "b8e9e764",
    "bids": [
        {"price": "0.001", "size": "701.99"},
        {"price": "0.002", "size": "57.5"},
        {"price": "0.79", "size": "600"},
        {"price": "0.8", "size": "300"},
    ],
    "asks": [
        {"price": "0.999", "size": "782"},
        {"price": "0.998", "size": "57.5"},
        {"price": "0.99", "size": "308"},
    ],
    "min_order_size": "5",
    "tick_size": "0.001",
    "neg_risk": False,
    "last_trade_price": "",
}

META = {"market_id": "1", "ticker": "TSLA", "outcome": "Yes", "outcome_index": 0}


def rows():
    return _levels(REAL_BOOK, META, "cyc", "2026-09-16T00:00:00+00:00")


def test_every_level_is_kept():
    """Full depth, both sides - never just top of book."""
    r = rows()
    assert len(r) == 7
    assert sum(1 for x in r if x["side"] == "bid") == 4
    assert sum(1 for x in r if x["side"] == "ask") == 3


def test_depth_rank_zero_is_best_on_both_sides():
    r = rows()
    best_bid = next(x for x in r if x["side"] == "bid" and x["depth_rank"] == 0)
    best_ask = next(x for x in r if x["side"] == "ask" and x["depth_rank"] == 0)
    assert best_bid["price"] == 0.8    # highest bid
    assert best_ask["price"] == 0.99   # lowest ask
    assert best_bid["price"] < best_ask["price"]


def test_depth_rank_orders_monotonically_away_from_touch():
    r = sorted((x for x in r_ if x["side"] == "ask"), key=lambda x: x["depth_rank"]) \
        if (r_ := rows()) else []
    assert [x["price"] for x in r] == [0.99, 0.998, 0.999]
    bids = sorted((x for x in rows() if x["side"] == "bid"), key=lambda x: x["depth_rank"])
    assert [x["price"] for x in bids] == [0.8, 0.79, 0.002, 0.001]


def test_level_rank_preserves_venue_order():
    r = [x for x in rows() if x["side"] == "bid"]
    assert [x["price"] for x in sorted(r, key=lambda x: x["level_rank"])] == \
        [0.001, 0.002, 0.79, 0.8]


def test_book_timestamp_and_hash_are_kept_separate_from_fetch_time():
    r = rows()[0]
    assert r["book_timestamp_utc"].startswith("2026-09-16T")
    assert r["book_hash"] == "b8e9e764"
    assert r["fetched_at_utc"] == "2026-09-16T00:00:00+00:00"
    assert r["book_timestamp_utc"] != r["fetched_at_utc"]


def test_sizes_and_prices_are_floats():
    for row in rows():
        assert isinstance(row["price"], float)
        assert isinstance(row["size"], float)


def test_malformed_levels_are_skipped_not_fatal():
    book = dict(REAL_BOOK, bids=[{"price": "abc", "size": "1"}, {"price": "0.5", "size": "2"}])
    r = [x for x in _levels(book, META, "c", "t") if x["side"] == "bid"]
    assert len(r) == 1 and r[0]["price"] == 0.5


def test_expiry_travels_with_every_level():
    """(ticker, strike, contract_type) is not unique across months.

    MSFT close_above 510 is open in four different months at once, so a group-by
    on those three columns blends four books unless the expiry rides along.
    """
    meta = dict(META, end_date="2026-09-30T20:00:00Z", period="monthly")
    r = _levels(REAL_BOOK, meta, "cyc", "2026-09-16T00:00:00+00:00")
    assert r and all(x["end_date"] == "2026-09-30T20:00:00Z" for x in r)
    assert all(x["period"] == "monthly" for x in r)
