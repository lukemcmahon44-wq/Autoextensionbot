"""Timezone-aware day math.

"Same day" and the daily-P&L reset must align to the *exchange's* trading day,
not the host clock. Kalshi settles on US Eastern time, so the production config
sets ``strategy.settlement_timezone: America/New_York``. On slim containers the
IANA database comes from the ``tzdata`` package (a dependency).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("kalshi.timeutil")


def resolve_tz(name: Optional[str]):
    """Return a tzinfo for ``name``, or None to mean 'use the host local time'.

    Never raises: an unknown/unavailable zone falls back to local with a warning
    so a bad config string can't crash the bot.
    """
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001
        log.warning("timezone %r unavailable (%s); falling back to local time", name, exc)
        return None


def day_key(ts: float, tz) -> str:
    """The calendar-day key (YYYY-MM-DD) of ``ts`` in timezone ``tz``."""
    dt = datetime.fromtimestamp(ts, tz) if tz is not None else datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d")


def same_calendar_day(a: float, b: float, tz) -> bool:
    return day_key(a, tz) == day_key(b, tz)
