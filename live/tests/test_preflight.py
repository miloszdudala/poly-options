from dataclasses import replace

import preflight

OPEN = lambda: {"blocked": False, "country": "ID", "ip": "x"}  # noqa: E731
BLOCKED = lambda: {"blocked": True, "country": "SG", "ip": "x"}  # noqa: E731
NOT_CLOSED = lambda: False  # noqa: E731


def boom():
    raise OSError("no network")


def test_ready(s):
    assert preflight.problems(s, "live", True, OPEN, NOT_CLOSED) == []


def test_blocked_country_refuses(s):
    out = preflight.problems(s, "live", True, BLOCKED, NOT_CLOSED)
    assert any("SG" in p for p in out)


def test_unknown_geoblock_refuses(s):
    assert preflight.problems(s, "live", True, boom, NOT_CLOSED)


def test_missing_blocked_field_counts_as_blocked(s):
    assert preflight.problems(s, "live", True, lambda: {}, NOT_CLOSED)


def test_unarmed_refuses(s):
    out = preflight.problems(replace(s, armed=False), "live", True, OPEN, NOT_CLOSED)
    assert any("armed" in p for p in out)


def test_no_key_refuses(s):
    assert preflight.problems(s, "live", False, OPEN, NOT_CLOSED)


def test_closed_only_refuses(s):
    assert preflight.problems(s, "live", True, OPEN, lambda: True)


def test_kill_switch_stops_dry_runs_too(s, kill_switch):
    kill_switch.write_text("stop")
    assert preflight.problems(s, "dry", False, BLOCKED, NOT_CLOSED)
    assert preflight.problems(s, "live", True, OPEN, NOT_CLOSED)


def test_dry_run_works_from_a_blocked_country(s):
    assert preflight.problems(replace(s, armed=False), "dry", False, BLOCKED, boom) == []
