"""
kalshi_client.py — All Kalshi REST API v2 calls isolated here.
Base URL: https://trading-api.kalshi.com/trade-api/v2
"""

import time
import logging
import traceback
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

# Exponential backoff settings for rate limiting
BACKOFF_START = 2      # seconds
BACKOFF_MAX = 60       # seconds


class KalshiClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        """
        Make an authenticated request with exponential backoff on 429s.
        Returns parsed JSON dict or None on error.
        """
        url = f"{BASE_URL}{path}"
        backoff = BACKOFF_START

        while True:
            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    logger.warning(
                        "Rate limited (429) on %s %s — backing off %ds",
                        method, path, backoff
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX)
                    continue  # retry

                if response.status_code == 401:
                    logger.error("Authentication failed (401) — check KALSHI_API_KEY")
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
        """Fetch a page of markets."""
        params = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/markets", params=params)

    def get_all_open_markets(self) -> list[dict]:
        """Paginate through all open markets and return a flat list."""
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
        """Fetch a single market by ticker."""
        data = self._request("GET", f"/markets/{ticker}")
        if data:
            return data.get("market")
        return None

    def get_market_orderbook(self, ticker: str, depth: int = 1) -> Optional[dict]:
        """Fetch the order book for a market."""
        return self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    # ------------------------------------------------------------------
    # Portfolio / position endpoints
    # ------------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        """Return all current positions."""
        data = self._request("GET", "/portfolio/positions")
        if data:
            return data.get("market_positions", [])
        return []

    def get_balance(self) -> Optional[dict]:
        """Return account balance."""
        return self._request("GET", "/portfolio/balance")

    # ------------------------------------------------------------------
    # Order endpoints
    # ------------------------------------------------------------------

    def create_order(
        self,
        ticker: str,
        side: str,         # "yes" or "no"
        action: str,       # "buy" or "sell"
        count: int,
        order_type: str,   # "limit" or "market"
        yes_price: Optional[int] = None,   # cents (1-99)
        no_price: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Place an order on Kalshi.

        Prices are in cents (1–99).  For a YES limit order pass yes_price.
        Returns the order dict or None on failure.
        """
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
        """Fetch a single order by ID."""
        data = self._request("GET", f"/portfolio/orders/{order_id}")
        if data:
            return data.get("order")
        return None

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel an open order."""
        data = self._request("DELETE", f"/portfolio/orders/{order_id}")
        return data

    def get_fills(self, ticker: str = None, limit: int = 100) -> list[dict]:
        """Return recent fills, optionally filtered by ticker."""
        params: dict = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        data = self._request("GET", "/portfolio/fills", params=params)
        if data:
            return data.get("fills", [])
        return []
