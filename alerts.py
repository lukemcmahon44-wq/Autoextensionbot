"""
alerts.py — Telegram alert logic isolated here.

Never raises — all errors are logged so the bot keeps running.
"""

import logging
import traceback
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 10  # seconds


def _send(token: str, chat_id: str, text: str) -> bool:
    """
    Send a Telegram message.  Returns True on success, False on any error.
    """
    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping alert")
        return False

    url = TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        if not resp.ok:
            logger.error(
                "Telegram error %s: %s", resp.status_code, resp.text[:200]
            )
            return False
        return True
    except Exception as exc:
        logger.error(
            "Telegram send failed: %s\n%s", exc, traceback.format_exc()
        )
        return False


def send_entry_alert(
    token: str,
    chat_id: str,
    ticker: str,
    price_cents: int,
    open_positions: int,
) -> None:
    """
    Format:  ENTER: [ticker] at [price]¢ | Positions open: [n]
    """
    text = (
        f"<b>ENTER:</b> {ticker} at {price_cents}¢ "
        f"| Positions open: {open_positions}"
    )
    ok = _send(token, chat_id, text)
    if ok:
        logger.info("Telegram entry alert sent for %s", ticker)


def send_exit_alert(
    token: str,
    chat_id: str,
    ticker: str,
    exit_price_cents: int,
    entry_price_cents: int,
    pnl_cents: int,
) -> None:
    """
    Format:  EXIT: [ticker] at [price]¢ | Entry was [entry]¢ | P&L: [+/-X]¢
    """
    pnl_str = f"{pnl_cents:+d}"
    text = (
        f"<b>EXIT:</b> {ticker} at {exit_price_cents}¢ "
        f"| Entry was {entry_price_cents}¢ "
        f"| P&amp;L: {pnl_str}¢"
    )
    ok = _send(token, chat_id, text)
    if ok:
        logger.info("Telegram exit alert sent for %s", ticker)


def send_info(token: str, chat_id: str, message: str) -> None:
    """Generic informational alert (e.g. bot startup/shutdown)."""
    _send(token, chat_id, message)
