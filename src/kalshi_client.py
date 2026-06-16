"""Thin, well-typed Kalshi trade-api v2 client.

We sign requests manually with ``cryptography`` rather than leaning on the
``kalshi-python`` SDK: the SDK's auth surface and the API host have both moved
more than once, and signing here is ~30 lines, transparent, and trivially
testable.

Auth (per https://docs.kalshi.com): every request carries
  KALSHI-ACCESS-KEY        = the API key id
  KALSHI-ACCESS-TIMESTAMP  = current unix time in milliseconds
  KALSHI-ACCESS-SIGNATURE  = base64( RSA-PSS-SHA256( timestamp + METHOD + path ) )
where ``path`` is the request path *including* the /trade-api/v2 prefix but
*excluding* the query string.

Note on "session expiry": RSA key auth is stateless -- there is no bearer token
to refresh. We re-sign (with a fresh timestamp) on every call, which also bounds
replay. So there is nothing to re-authenticate; transient failures are handled
by retry/backoff instead.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .models import OrderRequest

log = logging.getLogger("kalshi.client")

# HTTP statuses worth retrying (transient). 4xx (except 429) are caller errors.
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class KalshiAPIError(RuntimeError):
    """Non-retryable API error (4xx other than 429)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class KalshiTransientError(RuntimeError):
    """Retryable transient error (network or 5xx/429)."""


class KalshiClient:
    def __init__(
        self,
        api_base: str,
        api_key_id: str,
        private_key_path: str,
        *,
        timeout: float = 10.0,
        session: Optional[requests.Session] = None,
        time_in_force: Optional[str] = None,
        reduce_only_sells: bool = False,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key_id = api_key_id
        self.timeout = timeout
        self.session = session or requests.Session()
        # Optional exchange-level order controls. Left unset by default (plain
        # resting limit) so we only send fields we know Kalshi accepts. After
        # confirming the exact enum in a demo run you can opt in via config:
        #   order.time_in_force  -> e.g. an immediate/FOK semantic so entries don't rest
        #   order.reduce_only    -> guarantees a sell can only ever reduce a position
        self.time_in_force = time_in_force or None
        self.reduce_only_sells = bool(reduce_only_sells)
        with open(private_key_path, "rb") as fh:
            self._private_key = serialization.load_pem_private_key(fh.read(), password=None)

    # ----- signing --------------------------------------------------------
    def _headers(self, method: str, path: str) -> Dict[str, str]:
        ts_ms = str(int(time.time() * 1000))
        message = f"{ts_ms}{method.upper()}{path}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ----- transport ------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(KalshiTransientError),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}{endpoint}"
        path = urlsplit(url).path  # signed path excludes the query string
        try:
            resp = self.session.request(
                method.upper(),
                url,
                params=params,
                json=json_body,
                headers=self._headers(method, path),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise KalshiTransientError(f"network error: {exc}") from exc

        if resp.status_code in _RETRYABLE_STATUS:
            raise KalshiTransientError(f"transient {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise KalshiAPIError(resp.status_code, resp.text[:300])
        if not resp.content:
            return {}
        return resp.json()

    # ----- public reads ---------------------------------------------------
    def get_exchange_status(self) -> Dict[str, Any]:
        return self._request("GET", "/exchange/status")

    def get_balance_usd(self) -> float:
        """Available cash in USD. Kalshi reports the balance in cents."""
        data = self._request("GET", "/portfolio/balance")
        return float(data.get("balance", 0)) / 100.0

    def get_positions_raw(self) -> List[Dict[str, Any]]:
        """Raw market positions list straight from the exchange (source of truth)."""
        data = self._request("GET", "/portfolio/positions")
        return data.get("market_positions", []) or []

    def list_markets_page(
        self,
        *,
        status: str = "open",
        min_close_ts: Optional[int] = None,
        max_close_ts: Optional[int] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {"status": status, "limit": limit}
        if min_close_ts is not None:
            params["min_close_ts"] = int(min_close_ts)
        if max_close_ts is not None:
            params["max_close_ts"] = int(max_close_ts)
        if cursor:
            params["cursor"] = cursor
        data = self._request("GET", "/markets", params=params)
        return data.get("markets", []) or [], (data.get("cursor") or None)

    def list_all_markets(self, **kwargs) -> List[Dict[str, Any]]:
        """Walk cursor pagination and return every matching market."""
        out: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(100):  # hard page cap so a bad cursor can't loop forever
            page, cursor = self.list_markets_page(cursor=cursor, **kwargs)
            out.extend(page)
            if not cursor:
                break
        return out

    def get_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        data = self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}
        )
        return data.get("orderbook", {}) or {}

    # ----- writes ---------------------------------------------------------
    def create_order(self, order: OrderRequest) -> Dict[str, Any]:
        """Place a LIMIT order. client_order_id makes this idempotent server-side."""
        body = {
            "ticker": order.ticker,
            "action": order.action,
            "side": order.side,
            "type": "limit",            # never a naked market order
            "count": order.count,
            "client_order_id": order.client_order_id,
        }
        # YES-side limit price lives in yes_price; NO side would use no_price.
        if order.side == "yes":
            body["yes_price"] = order.price_cents
        else:
            body["no_price"] = order.price_cents
        # Optional, opt-in controls (only sent when configured).
        if self.time_in_force:
            body["time_in_force"] = self.time_in_force
        if self.reduce_only_sells and order.action == "sell":
            body["reduce_only"] = True
        data = self._request("POST", "/portfolio/orders", json_body=body)
        return data.get("order", data)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/portfolio/orders/{order_id}")
