"""Parser tests. Every string here was taken verbatim from the live Gamma API."""
import pytest

from poly_parse import parse_market, parse_ticker, parse_period, to_float


# ------------------------------------------------------------ close_above

@pytest.mark.parametrize("question,event,strike,period", [
    ("Will Tesla (TSLA) close above $290 end of September?",
     "Will Tesla (TSLA) close above ___ end of September?", 290.0, "monthly"),
    ("Will Apple (AAPL) close above $320 on September 16?",
     "Apple (AAPL) closes above ___ on September 16?", 320.0, "daily"),
    ("Will Apple (AAPL) finish week of September 14 above $300?",
     "Will Apple (AAPL) finish week of September 14 above___?", 300.0, "weekly"),
    ("Will Microsoft (MSFT) close above $1,250 end of September?",
     "", 1250.0, "monthly"),
])
def test_close_above(question, event, strike, period):
    p = parse_market(question, event_title=event, outcomes=["Yes", "No"])
    assert p.contract_type == "close_above"
    assert p.strike == strike
    assert p.direction == "above"
    assert p.period == period


# ------------------------------------------------------------------- hit

@pytest.mark.parametrize("question,ctype,barrier,direction", [
    ("Will Apple (AAPL) hit (HIGH) $360 in September?", "hit_up", 360.0, "above"),
    ("Will Apple (AAPL) hit (LOW) $304 in September?", "hit_down", 304.0, "below"),
    ("Will S&P 500 (SPX) hit (HIGH) $7,800 in September?", "hit_up", 7800.0, "above"),
])
def test_hit(question, ctype, barrier, direction):
    p = parse_market(question, outcomes=["Yes", "No"])
    assert p.contract_type == ctype
    assert p.barrier == barrier
    assert p.direction == direction
    assert p.strike is None  # a barrier is never recorded as a strike


def test_hit_from_group_item_title_arrows():
    up = parse_market("", group_item_title="↑ $360", outcomes=["Yes", "No"])
    assert (up.contract_type, up.barrier) == ("hit_up", 360.0)
    dn = parse_market("", group_item_title="↓ $288", outcomes=["Yes", "No"])
    assert (dn.contract_type, dn.barrier) == ("hit_down", 288.0)


# ----------------------------------------------------------------- range

def test_range_band():
    p = parse_market(
        "Will Apple (AAPL) close at $310-$315 on the final day of trading of the week of Sep 14 - Sep 18?",
        outcomes=["Yes", "No"])
    assert p.contract_type == "range"
    assert (p.range_low, p.range_high) == (310.0, 315.0)
    assert p.period == "weekly"


def test_range_open_ended():
    lo = parse_market("Will Apple (AAPL) close at <$310 on the final day of trading of the week?",
                      outcomes=["Yes", "No"])
    assert lo.contract_type == "range" and lo.range_high == 310.0 and lo.range_low is None
    hi = parse_market("Will Apple (AAPL) close at >$355 on the final day of trading of the week?",
                      outcomes=["Yes", "No"])
    assert hi.contract_type == "range" and hi.range_low == 355.0 and hi.range_high is None


# --------------------------------------------------------------- up_down

def test_up_down_by_outcomes():
    p = parse_market("Apple (AAPL) Up or Down on September 16?",
                     event_title="Apple (AAPL) Up or Down on September 16?",
                     outcomes=["Up", "Down"])
    assert p.contract_type == "up_down"
    assert p.ticker == "AAPL"
    assert p.strike is None and p.barrier is None


def test_up_down_detected_from_outcomes_alone():
    p = parse_market("Some unusual phrasing", outcomes=["Up", "Down"])
    assert p.contract_type == "up_down"


# -------------------------------------------------------------- failures

@pytest.mark.parametrize("question", [
    "Will the Fed cut rates in September?",
    "Who will win the election?",
    "",
])
def test_unparsed_is_flagged_not_guessed(question):
    p = parse_market(question, outcomes=["Yes", "No"])
    assert p.contract_type == "unparsed"
    assert p.parse_reason
    assert p.strike is None and p.barrier is None


# --------------------------------------------------------------- tickers

@pytest.mark.parametrize("title,ticker", [
    ("Will Tesla (TSLA) close above ___ end of September?", "TSLA"),
    ("What will Gold (XAUUSD) hit in September 2026?", "XAUUSD"),
    ("What will S&P 500 (SPX) hit by end of December?", "SPX"),
    ("No ticker here at all", None),
])
def test_parse_ticker(title, ticker):
    assert parse_ticker(title) == ticker


def test_to_float_handles_separators():
    assert to_float("$7,744.9999") == 7744.9999
    assert to_float("290") == 290.0
    assert to_float("not a number") is None
    assert to_float(None) is None


@pytest.mark.parametrize("question,ctype,barrier", [
    ("Will S&P 500 (SPX) hit $9,000 (HIGH) in December?", "hit_up", 9000.0),
    ("Will S&P 500 (SPX) hit $7,400 (LOW) in December?", "hit_down", 7400.0),
])
def test_hit_number_before_qualifier(question, ctype, barrier):
    """Second live ordering: the level precedes (HIGH)/(LOW)."""
    p = parse_market(question, outcomes=["Yes", "No"])
    assert p.contract_type == ctype
    assert p.barrier == barrier


def test_hit_parses_without_group_item_title():
    """Both hit orderings must parse from the question alone."""
    for q in ("Will Apple (AAPL) hit (HIGH) $360 in September?",
              "Will S&P 500 (SPX) hit $9,000 (HIGH) in December?"):
        assert parse_market(q, group_item_title=None,
                            outcomes=["Yes", "No"]).contract_type == "hit_up"
