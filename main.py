"""
main.py — Entry point. Starts the Kalshi trading bot loop.

Usage:
    python main.py
"""

import logging
import logging.handlers
import os
import sys
import time
import traceback

from dotenv import load_dotenv

import db
from bot import TradingBot
import alerts

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------
load_dotenv()

KALSHI_API_KEY_ID      = os.environ.get("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "kalshi_private_key.pem")
TELEGRAM_TOKEN         = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
SCAN_INTERVAL_SEC      = int(os.environ.get("SCAN_INTERVAL_SEC", "30"))
LOG_FILE               = os.environ.get("LOG_FILE", "bot.log")
DB_PATH                = os.environ.get("DB_PATH", "trades.db")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_file: str = LOG_FILE) -> None:
    """Configure structured logging to console + rotating file."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%SZ"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(console)

    # Rotating file handler — 10 MB, keep 3 backups
    rotating = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    rotating.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(rotating)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()

    if not KALSHI_API_KEY_ID:
        logger.error("KALSHI_API_KEY_ID is not set — cannot start. Check your .env file.")
        sys.exit(1)

    logger.info("=== Kalshi Trading Bot starting ===")
    logger.info(
        "Config: scan_interval=%ds | db=%s | log=%s",
        SCAN_INTERVAL_SEC, DB_PATH, LOG_FILE
    )

    # Initialise database
    db.init_db(DB_PATH)

    # Create bot
    bot = TradingBot(
        api_key_id=KALSHI_API_KEY_ID,
        private_key_path=KALSHI_PRIVATE_KEY_PATH,
        telegram_token=TELEGRAM_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
    )

    # Print all-time P&L on startup
    bot.print_startup_summary()

    # Notify Telegram that bot has started
    alerts.send_info(
        TELEGRAM_TOKEN,
        TELEGRAM_CHAT_ID,
        "🤖 Kalshi trading bot <b>started</b>.",
    )

    cycle = 0
    while True:
        cycle += 1
        try:
            logger.info("--- Cycle %d starting ---", cycle)

            # 1. Check exits first (protect capital)
            bot.watch_and_sell()

            # 2. Scan for new entries
            bot.scan_and_buy()

            logger.info(
                "--- Cycle %d complete | open positions: %d ---",
                cycle, len(bot.open_positions)
            )

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received — shutting down.")
            alerts.send_info(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, "🛑 Kalshi bot stopped (KeyboardInterrupt).")
            break
        except Exception as exc:
            logger.error(
                "Unhandled exception in main loop: %s\n%s",
                exc, traceback.format_exc()
            )

        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    main()
