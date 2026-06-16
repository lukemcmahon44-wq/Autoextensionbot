"""Timezone-aware day math used by the same-day filter and the daily-loss reset."""
from datetime import datetime
from zoneinfo import ZoneInfo

from src.timeutil import day_key, resolve_tz, same_calendar_day

ET = ZoneInfo("America/New_York")


def test_same_eastern_day_true():
    tz = resolve_tz("America/New_York")
    a = datetime(2026, 1, 1, 9, 0, tzinfo=ET).timestamp()
    b = datetime(2026, 1, 1, 23, 30, tzinfo=ET).timestamp()
    assert same_calendar_day(a, b, tz) is True


def test_crosses_eastern_midnight_is_not_same_day():
    tz = resolve_tz("America/New_York")
    a = datetime(2026, 1, 1, 23, 30, tzinfo=ET).timestamp()
    b = datetime(2026, 1, 2, 0, 30, tzinfo=ET).timestamp()
    assert same_calendar_day(a, b, tz) is False


def test_eastern_evening_differs_from_utc_day():
    # 2026-01-01 22:00 ET == 2026-01-02 03:00 UTC. Same Eastern day, but a naive
    # UTC reading would call it a different day -- exactly the bug we avoid.
    tz = resolve_tz("America/New_York")
    a = datetime(2026, 1, 1, 20, 0, tzinfo=ET).timestamp()
    b = datetime(2026, 1, 1, 22, 0, tzinfo=ET).timestamp()
    assert day_key(a, tz) == "2026-01-01"
    assert day_key(b, tz) == "2026-01-01"
    assert same_calendar_day(a, b, tz) is True


def test_unknown_or_missing_tz_falls_back():
    assert resolve_tz("Not/AZone") is None
    assert resolve_tz(None) is None
