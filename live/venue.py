"""Everything that talks to Polymarket. The only file importing its SDK.

`polymarket-client` (the unified SDK, 0.13.0) is the client because the
original `py-clob-client` stopped working at the CLOB V2 cutover on
2026-04-28, and its V2 successor `py-clob-client-v2` is already deprecated.

Reads go through the public client and need no key. The authenticated client
is built only when something needs it, so a dry run never touches the key.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

import requests

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
PUSD_DECIMALS = 6


def geoblock() -> dict:
    """Polymarket's own answer for this connection: {blocked, ip, country, region}.

    `blocked` is true for close-only countries too (Singapore, Thailand,
    Poland, ... measured 2026-10-17): no new orders from there.
    """
    resp = requests.get(GEOBLOCK_URL, timeout=15)
    resp.raise_for_status()
    return resp.json()


class Venue:
    def __init__(self, secrets: dict[str, str] | None = None):
        from polymarket import PublicClient

        self._secrets = secrets or {}
        self._public = PublicClient()
        self._secure = None

    @property
    def has_key(self) -> bool:
        return bool(self._secrets.get("POLYMARKET_PRIVATE_KEY"))

    @property
    def secure(self):
        """Authenticated client. Creating it derives the API credentials and
        sets up the Deposit Wallet if the key has none yet."""
        if self._secure is None:
            from polymarket import SecureClient

            if not self.has_key:
                raise RuntimeError("POLYMARKET_PRIVATE_KEY is not set (live/.env)")
            self._secure = SecureClient.create(
                private_key=self._secrets["POLYMARKET_PRIVATE_KEY"],
                wallet=self._secrets.get("POLYMARKET_WALLET_ADDRESS"))
        return self._secure

    # -- reads, no key ------------------------------------------------------

    def book(self, token_id: str) -> dict:
        """Asks best first as (price, size) floats, plus the venue's minimums.

        The CLOB returns levels worst-price-first (README), so they are sorted
        here rather than trusted.
        """
        b = self._public.get_order_book(token_id=token_id)
        asks = sorted((float(lvl.price), float(lvl.size)) for lvl in b.asks)
        return {"asks": asks, "min_order_size": float(b.min_order_size),
                "tick_size": float(b.tick_size), "condition_id": str(b.condition_id)}

    def books(self, token_ids: list[str], chunk: int = 100) -> dict[str, list[tuple[float, float]]]:
        """Asks (best first) for many tokens, in batches. A token the venue no
        longer quotes is simply absent from the result."""
        out: dict[str, list[tuple[float, float]]] = {}
        ids = list(dict.fromkeys(token_ids))
        for i in range(0, len(ids), chunk):
            for b in self._public.get_order_books(token_ids=ids[i:i + chunk]):
                out[str(b.asset_id)] = sorted((float(l.price), float(l.size)) for l in b.asks)
        return out

    # -- account, key needed ------------------------------------------------

    def wallet(self) -> str:
        return str(self.secure.wallet)

    def closed_only(self) -> bool:
        return bool(self.secure.get_closed_only_mode())

    def balance_usd(self) -> float:
        raw = self.secure.get_balance_allowance(asset_type="COLLATERAL").balance
        return raw / 10 ** PUSD_DECIMALS

    def approvals(self):
        return self.secure.get_trading_approvals_state()

    def setup_approvals(self) -> None:
        self.secure.setup_trading_approvals()

    def positions(self, status: str | None = None) -> list:
        return list(self.secure.list_positions(status=status).iter_items())

    def redeem(self, condition_id: str):
        return self.secure.redeem_positions(condition_id=condition_id).wait()

    def buy(self, token_id: str, amount_usd: float, max_price: float,
            max_spend_usd: float) -> dict:
        """Fill-and-kill BUY: spend up to `amount_usd` on shares priced at or
        under `max_price`; whatever does not fill at once is cancelled. Nothing
        rests in the book, so there is never an open order to manage.
        `max_spend_usd` caps price plus fee: the SDK shrinks the order if the
        venue's fee would push the total past it.

        Returns a plain dict that always carries `ok`; the SDK's errors are
        caught and recorded rather than raised, because one market refusing an
        order must not stop the next.
        """
        from polymarket import PolymarketError

        amount = Decimal(str(amount_usd)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        try:
            r = self.secure.place_market_order(
                token_id=token_id, side="BUY", amount=amount,
                max_price=Decimal(str(max_price)),
                max_spend=Decimal(str(max_spend_usd)).quantize(Decimal("0.01"), rounding=ROUND_DOWN),
                order_type="FAK")
        except PolymarketError as exc:
            return {"ok": False, "code": type(exc).__name__, "message": str(exc)}
        if not r.ok:
            return {"ok": False, "code": r.code, "message": r.message}
        # For a BUY the maker side is the collateral paid, the taker side the
        # shares received. The units are what the CLOB sends and have not been
        # seen on a real fill yet; trader.py halts if they do not add up.
        return {"ok": True, "order_id": r.order_id, "status": r.status,
                "spent_usd": float(r.making_amount), "shares": float(r.taking_amount),
                "trade_ids": list(r.trade_ids)}
