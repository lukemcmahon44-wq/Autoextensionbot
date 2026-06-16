"""Alerting. Posts to ALERT_WEBHOOK_URL on trades, halts, and a daily summary.

Notification failures must NEVER crash the trading loop -- every send is wrapped
and degraded to a log line on error.
"""
from __future__ import annotations

import logging
from typing import Optional

import requests

log = logging.getLogger("kalshi.notifier")


class Notifier:
    def __init__(self, webhook_url: Optional[str], *, timeout: float = 5.0, paper: bool = True):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.tag = "[PAPER]" if paper else "[LIVE]"

    def _send(self, text: str) -> None:
        log.info("ALERT %s %s", self.tag, text)
        if not self.webhook_url:
            return
        try:
            # {"text": ...} is the lingua franca for Slack/Discord/most webhooks.
            requests.post(self.webhook_url, json={"text": f"{self.tag} {text}"}, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 -- alerting must never break trading
            log.warning("notifier: webhook post failed (ignored): %s", exc)

    def startup(self, summary: str) -> None:
        self._send(f"STARTED — {summary}")

    def trade(self, action: str, ticker: str, count: int, price_cents: int, reason: str = "") -> None:
        suffix = f" ({reason})" if reason else ""
        self._send(f"TRADE {action.upper()} {ticker} x{count} @ {price_cents}c{suffix}")

    def halt(self, reason: str, flattened: bool = False) -> None:
        self._send(f"HALT: {reason}" + (" — flattened all positions" if flattened else ""))

    def kill(self, reason: str) -> None:
        self._send(f"KILL SWITCH: {reason}")

    def error(self, message: str) -> None:
        self._send(f"ERROR: {message}")

    def daily_summary(self, equity: float, pnl_pct: float, positions: int) -> None:
        self._send(
            f"DAILY SUMMARY equity=${equity:.2f} day_pnl={pnl_pct:+.2f}% open_positions={positions}"
        )
