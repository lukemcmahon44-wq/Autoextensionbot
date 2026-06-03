"""Loguru setup: console + rotating file; every line traceable by lead_id.

Usage:
    from voiceagent.observability.logging import configure_logging, for_lead
    configure_logging()
    log = for_lead(lead_id)
    log.info("placing call to {}", lead.phone)
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from voiceagent.config import get_settings

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_configured = False


def _inject_lead_id(record) -> bool:
    record["extra"].setdefault("lead_id", "-")
    return True


def configure_logging(level: str | None = None) -> None:
    """Idempotently configure console + rotating-file sinks."""
    global _configured
    if _configured:
        return
    settings = get_settings()
    lvl = (level or settings.log_level or "INFO").upper()
    _LOG_DIR.mkdir(exist_ok=True)

    logger.remove()
    logger.add(
        sys.stderr,
        level=lvl,
        filter=_inject_lead_id,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "lead=<cyan>{extra[lead_id]}</cyan> | {message}"
        ),
    )
    logger.add(
        _LOG_DIR / "voiceagent.log",
        level=lvl,
        rotation="20 MB",
        retention="14 days",
        compression="zip",
        enqueue=True,
        filter=_inject_lead_id,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | lead={extra[lead_id]} | {message}",
    )
    _configured = True


def for_lead(lead_id):
    """Return a logger bound to a lead_id so every call is traceable."""
    return logger.bind(lead_id=lead_id)
