"""
kalshi_client.py — All Kalshi REST API v2 calls isolated here.
Base URL: https://trading-api.kalshi.com/trade-api/v2
Auth: RSA private key signing (KALSHI-ACCESS-KEY / KALSHI-ACCESS-SIGNATURE)
"""

import base64
import time
import logging
import traceback
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

BACKOFF_START = 2
BACKOFF_MAX = 60


class KalshiClient:
    def __init__(self, api_key_id: str, private_key_path: str):
        self.api_key_id = api_key_id

        # Load RSA private key from PEM file
        with open(private_key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _sign(self, timestamp_ms: int, method: str, path: str) -> str:
        """Sign the request using RSA-SHA256 and return base64 signature."""
        message = f"{timestamp_ms}{method.upper()}{path}".encode()
        signature = self.private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode()

    def _auth_headers(self, method: str, path: str) -> dict:
        """Build Kalshi RSA auth headers for a request."""
        timestamp_ms = int(time.time() * 1000)
        signature = self._sign(timestamp_ms, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
        }

    def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        """
        Make an authenticated request with exponential backoff on 429s.
        Returns parsed JSON dict or None on error.
        """
        url = f"{BASE_URL}{path}"
        backoff = BACKOFF_START

        while True:
            try:
                headers = self._auth_headers(method, path)
                response = self.session.request(method, url, headers=headers, **kwargs)

                if response.status_code == 429:
                    logger.warning(
                        "Rate limited (429) on %s %s — backing off %ds",
                        method, path, backoff
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX)
                    continue

                if response.status_code == 401:
                    logger.error("Authentication failed (401) — check your API key and private key")
                    return None

                if not response.ok:
                    logger.error(
                        "HTTP %s on %s %s: %s",
                        response.status_code, method, path, response.text[:500]
                    )
                    return None

                return response.json()

            except requests.exceptions.RequestException as exc:
                logger.error(
                    "Network error on %s %s: %s\n%s",
                    method, path, exc, traceback.format_exc()
                )
                return None
            except Exception as exc:
                logger.error(
                    "Unexpected error on %s %s: %s\n%s",
                    method, path, exc, traceback.format_exc()
                )
                return None

    # ------------------------------------------------------------------
    # Market endpoints
    # ------------------------------------------------------------------

    def get_markets(self, status: str = "open", limit: int = 1000, cursor: str = None) -> Optional[dict]:
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/markets", params=params)

    def get_all_open_markets(self) -> list[dict]:
        markets = []
        cursor = None
        while True:
            data = self.get_markets(status="open", limit=1000, cursor=cursor)
            if not data:
                break
            batch = data.get("markets", [])
            markets.extend(batch)
            cursor = data.get("cursor")
            if not cursor or not batch:
                break
        return markets

    def get_market(self, ticker: str) -> Optional[dict]:
        data = self._request("GET", f"/markets/{ticker}")
        if data:
            return data.get("market")
        return None

    def get_market_orderbook(self, ticker: str, depth: int = 1) -> Optional[dict]:
        return self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    # ------------------------------------------------------------------
    # Portfolio / position endpoints
    # ------------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        data = self._request("GET", "/portfolio/positions")
        if data:
            return data.get("market_positions", [])
        return []

    def get_balance(self) -> Optional[dict]:
        return self._request("GET", "/portfolio/balance")

    # ------------------------------------------------------------------
    # Order endpoints
    # ------------------------------------------------------------------

    def create_order(
        self,
        ticker: str,
        side: str,
        action: str,
        count: int,
        order_type: str,
        yes_price: Optional[int] = None,
        no_price: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        payload: dict = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
        }
        if yes_price is not None:
            payload["yes_price"] = yes_price
        if no_price is not None:
            payload["no_price"] = no_price
        if client_order_id:
            payload["client_order_id"] = client_order_id

        data = self._request("POST", "/portfolio/orders", json=payload)
        if data:
            return data.get("order")
        return None

    def get_order(self, order_id: str) -> Optional[dict]:
        data = self._request("GET", f"/portfolio/orders/{order_id}")
        if data:
            return data.get("order")
        return None

    def cancel_order(self, order_id: str) -> Optional[dict]:
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_fills(self, ticker: str = None, limit: int = 100) -> list[dict]:
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/fills", params=params)
        if data:
            return data.get("fills", [])
        return []
