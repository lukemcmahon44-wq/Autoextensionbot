"""
bot.py — Core trading logic: scanner, order placement, exit watcher.
"""

import logging
import traceback
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from kalshi_client import KalshiClient
import db
import alerts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trading parameters
# ---------------------------------------------------------------------------
BUY_THRESHOLD_CENTS = 97      # Buy YES if best YES ask >= this
SELL_THRESHOLD_CENTS = 65     # Sell YES if best YES ask drops below this
MAX_POSITIONS = 10            # Never hold more than this many open positions
MIN_VOLUME = 50               # Skip markets with fewer contracts of volume
MIN_HOURS_TO_EXPIRY = 1       # Skip markets expiring sooner than this
CONTRACTS_PER_TRADE = 1       # Always trade 1 contract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hours_to_expiry(market: dict) -> Optional[float]:
    """Return hours until market closes, or None if unparseable."""
    close_time_str = market.get("close_time") or market.get("expiration_time")
    if not close_time_str:
        return None
    try:
        # Kalshi returns ISO-8601 strings (Z suffix)
        close_time_str = close_time_str.replace("Z", "+00:00")
        close_dt = datetime.fromisoformat(close_time_str)
        now = datetime.now(timezone.utc)
        delta = close_dt - now
        return delta.total_seconds() / 3600
    except Exception:
        return None


def _best_yes_ask(market: dict) -> Optional[int]:
    """
    Extract the best YES ask price in cents from a market dict.
    Kalshi markets expose yes_ask / no_ask at the top level.
    Returns None if unavailable.
    """
    # Prices are stored as integers in cents (1–99)
    price = market.get("yes_ask")
    if price is not None:
        return int(price)
    return None


def _best_yes_bid(market: dict) -> Optional[int]:
    """Extract the best YES bid price in cents from a market dict."""
    price = market.get("yes_bid")
    if price is not None:
        return int(price)
    return None


def _market_volume(market: dict) -> int:
    """Return total volume for a market (number of contracts traded)."""
    return int(market.get("volume", 0) or 0)


def _unique_order_id(prefix: str = "bot") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------

class TradingBot:
    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
        telegram_token: str,
        telegram_chat_id: str,
    ):
        self.client = KalshiClient(api_key_id, private_key_path)
        self.tg_token = telegram_token
        self.tg_chat_id = telegram_chat_id

        # In-memory map: ticker -> {row_id, entry_price}
        # Loaded from DB on startup so restarts are safe
        self.open_positions: dict[str, dict] = {}
        self._restore_positions_from_db()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _restore_positions_from_db(self) -> None:
        """Reload open positions from SQLite so restarts are idempotent."""
        open_trades = db.get_open_trades()
        for trade in open_trades:
            self.open_positions[trade["ticker"]] = {
                "row_id": trade["id"],
                "entry_price": trade["entry_price"],
                "contracts": trade["contracts"],
            }
        if self.open_positions:
            logger.info(
                "Restored %d open position(s) from DB: %s",
                len(self.open_positions),
                list(self.open_positions.keys()),
            )

    def print_startup_summary(self) -> None:
        """Print all-time P&L summary to console/log."""
        summary = db.get_pnl_summary()
        if not summary:
            logger.info("No trade history found.")
            return

        total = summary.get("total_pnl_cents", 0)
        sign = "+" if total >= 0 else ""
        logger.info(
            "=== ALL-TIME P&L SUMMARY ===\n"
            "  Total trades  : %d  (closed: %d, open: %d)\n"
            "  Wins / Losses : %d / %d\n"
            "  Total P&L     : %s%d¢  ($%.2f)",
            summary.get("total_trades", 0),
            summary.get("closed_trades", 0),
            summary.get("open_trades", 0),
            summary.get("wins", 0),
            summary.get("losses", 0),
            sign, total, total / 100,
        )

    # ------------------------------------------------------------------
    # Scanning — buy side
    # ------------------------------------------------------------------

    def scan_and_buy(self) -> None:
        """
        Scan all open markets, apply filters, and buy qualifying ones.
        """
        if len(self.open_positions) >= MAX_POSITIONS:
            logger.info(
                "At max positions (%d/%d) — skipping buy scan",
                len(self.open_positions), MAX_POSITIONS
            )
            return

        markets = self.client.get_all_open_markets()
        logger.info(
            "Scan: %d open markets found | open positions: %d/%d",
            len(markets), len(self.open_positions), MAX_POSITIONS
        )

        bought = 0
        for market in markets:
            if len(self.open_positions) >= MAX_POSITIONS:
                break

            ticker = market.get("ticker")
            if not ticker:
                continue

            # ---- Skip if already in a position ----
            if ticker in self.open_positions:
                continue

            # ---- Volume filter ----
            if _market_volume(market) < MIN_VOLUME:
                continue

            # ---- Expiry filter ----
            hours = _hours_to_expiry(market)
            if hours is None or hours < MIN_HOURS_TO_EXPIRY:
                continue

            # ---- Price filter ----
            yes_ask = _best_yes_ask(market)
            if yes_ask is None or yes_ask < BUY_THRESHOLD_CENTS:
                continue

            # ---- Place buy order ----
            logger.info(
                "BUY signal: %s yes_ask=%d¢ volume=%d hours_left=%.1f",
                ticker, yes_ask, _market_volume(market), hours or 0
            )
            self._buy(ticker, yes_ask)
            bought += 1

        if bought:
            logger.info("Bought %d new position(s) this scan cycle", bought)

    def _buy(self, ticker: str, yes_ask: int) -> None:
        """Place a market buy order for YES contracts."""
        client_oid = _unique_order_id("buy")
        try:
            order = self.client.create_order(
                ticker=ticker,
                side="yes",
                action="buy",
                count=CONTRACTS_PER_TRADE,
                order_type="market",
                client_order_id=client_oid,
            )
        except Exception as exc:
            logger.error(
                "Exception placing buy order for %s: %s\n%s",
                ticker, exc, traceback.format_exc()
            )
            return

        if not order:
            logger.error("Buy order failed for %s — skipping", ticker)
            return

        order_id = order.get("order_id") or order.get("id")
        # Use yes_ask as entry price proxy; fills may differ slightly
        entry_price = order.get("yes_price") or yes_ask

        row_id = db.record_entry(
            ticker=ticker,
            entry_price=entry_price,
            contracts=CONTRACTS_PER_TRADE,
            entry_order_id=order_id,
        )

        self.open_positions[ticker] = {
            "row_id": row_id,
            "entry_price": entry_price,
            "contracts": CONTRACTS_PER_TRADE,
        }

        logger.info(
            "ENTERED %s at %d¢ — order_id=%s | open positions: %d",
            ticker, entry_price, order_id, len(self.open_positions)
        )

        alerts.send_entry_alert(
            self.tg_token,
            self.tg_chat_id,
            ticker=ticker,
            price_cents=entry_price,
            open_positions=len(self.open_positions),
        )

    # ------------------------------------------------------------------
    # Exit watcher
    # ------------------------------------------------------------------

    def watch_and_sell(self) -> None:
        """
        Check all open positions. Sell any where the best YES ask has
        dropped below SELL_THRESHOLD_CENTS.
        """
        if not self.open_positions:
            return

        tickers = list(self.open_positions.keys())
        for ticker in tickers:
            try:
                market = self.client.get_market(ticker)
                if not market:
                    logger.warning("Could not fetch market data for %s", ticker)
                    continue

                yes_ask = _best_yes_ask(market)
                if yes_ask is None:
                    logger.warning("No yes_ask price for %s", ticker)
                    continue

                if yes_ask < SELL_THRESHOLD_CENTS:
                    logger.info(
                        "EXIT signal: %s yes_ask=%d¢ (below %d¢ threshold)",
                        ticker, yes_ask, SELL_THRESHOLD_CENTS
                    )
                    self._sell(ticker, market)

            except Exception as exc:
                logger.error(
                    "Error watching position %s: %s\n%s",
                    ticker, exc, traceback.format_exc()
                )

    def _sell(self, ticker: str, market: dict) -> None:
        """
        Place a limit sell order at the current best bid price.
        """
        pos = self.open_positions.get(ticker)
        if not pos:
            return

        best_bid = _best_yes_bid(market)
        if best_bid is None or best_bid <= 0:
            logger.warning(
                "No valid best bid for %s — cannot place sell order", ticker
            )
            return

        client_oid = _unique_order_id("sell")
        try:
            order = self.client.create_order(
                ticker=ticker,
                side="yes",
                action="sell",
                count=pos["contracts"],
                order_type="limit",
                yes_price=best_bid,
                client_order_id=client_oid,
            )
        except Exception as exc:
            logger.error(
                "Exception placing sell order for %s: %s\n%s",
                ticker, exc, traceback.format_exc()
            )
            return

        if not order:
            logger.error("Sell order failed for %s — skipping", ticker)
            return

        order_id = order.get("order_id") or order.get("id")
        exit_price = order.get("yes_price") or best_bid

        db.record_exit(
            row_id=pos["row_id"],
            exit_price=exit_price,
            exit_order_id=order_id,
        )

        entry_price = pos["entry_price"]
        pnl = (exit_price - entry_price) * pos["contracts"]

        logger.info(
            "EXITED %s at %d¢ — entry was %d¢ — P&L: %+d¢ — order_id=%s",
            ticker, exit_price, entry_price, pnl, order_id
        )

        alerts.send_exit_alert(
            self.tg_token,
            self.tg_chat_id,
            ticker=ticker,
            exit_price_cents=exit_price,
            entry_price_cents=entry_price,
            pnl_cents=pnl,
        )

        del self.open_positions[ticker]
