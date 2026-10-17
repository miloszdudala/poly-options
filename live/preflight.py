"""What must be true before an order may leave this machine.

Every check runs before every live run, not once at setup. A trip that ends
mid-week moves the laptop back into a blocked country, and the schedule keeps
firing.
"""
from __future__ import annotations

from typing import Callable

from settings import KILL_SWITCH, Settings


def problems(s: Settings, mode: str, has_key: bool,
             geoblock: Callable[[], dict], closed_only: Callable[[], bool]) -> list[str]:
    """Reasons this run must not trade. Empty means go.

    `geoblock` and `closed_only` are passed in so tests need no network. A
    dry run checks only the kill switch: it never sends anything, and it
    should work from anywhere, including from a blocked country.
    """
    out: list[str] = []
    if KILL_SWITCH.exists():
        out.append(f"kill switch {KILL_SWITCH} exists; delete it to resume")
    if mode != "live":
        return out
    if not s.armed:
        out.append("config.yaml has armed: false")
    if not has_key:
        out.append("POLYMARKET_PRIVATE_KEY is not set (live/.env)")
    try:
        geo = geoblock()
        if geo.get("blocked", True):
            out.append(f"Polymarket blocks new orders from this connection "
                       f"(country {geo.get('country')!r}, ip {geo.get('ip')})")
    except Exception as exc:     # unknown is not allowed
        out.append(f"geoblock check failed: {exc}")
    if has_key and not out:
        try:
            if closed_only():
                out.append("the account is in closed-only mode")
        except Exception as exc:
            out.append(f"closed-only check failed: {exc}")
    return out
