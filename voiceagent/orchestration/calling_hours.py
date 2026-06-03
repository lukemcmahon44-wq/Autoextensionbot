"""Calling-hours enforcement: never dial outside [start, end) in the LEAD's
local timezone. Out-of-window leads are queued (left selectable), not skipped.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "America/New_York"


def _zone(tz_name: Optional[str], default_tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or default_tz)
    except Exception:
        return ZoneInfo(default_tz)


def is_within_calling_hours(
    tz_name: Optional[str],
    now: Optional[datetime] = None,
    start_hour: int = 8,
    end_hour: int = 20,
    default_tz: str = _DEFAULT_TZ,
) -> bool:
    zone = _zone(tz_name, default_tz)
    local = now.astimezone(zone) if now else datetime.now(zone)
    return start_hour <= local.hour < end_hour


def seconds_until_window(
    tz_name: Optional[str],
    now: Optional[datetime] = None,
    start_hour: int = 8,
    end_hour: int = 20,
    default_tz: str = _DEFAULT_TZ,
) -> int:
    """0 if currently callable; otherwise seconds until the next window opens."""
    zone = _zone(tz_name, default_tz)
    local = now.astimezone(zone) if now else datetime.now(zone)
    if start_hour <= local.hour < end_hour:
        return 0
    target = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if local.hour >= end_hour:
        target += timedelta(days=1)
    return int((target - local).total_seconds())
