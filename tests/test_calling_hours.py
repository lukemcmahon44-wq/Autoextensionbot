from datetime import datetime
from zoneinfo import ZoneInfo

from voiceagent.orchestration.calling_hours import is_within_calling_hours, seconds_until_window

ET = ZoneInfo("America/New_York")


def test_within_window():
    noon = datetime(2026, 6, 3, 12, 0, tzinfo=ET)
    assert is_within_calling_hours("America/New_York", noon, 8, 20)


def test_outside_window_at_night():
    night = datetime(2026, 6, 3, 23, 0, tzinfo=ET)
    assert not is_within_calling_hours("America/New_York", night, 8, 20)
    assert seconds_until_window("America/New_York", night, 8, 20) > 0


def test_timezone_matters():
    # 9pm ET == 6pm PT: outside for NY, inside for LA.
    t = datetime(2026, 6, 3, 21, 0, tzinfo=ET)
    assert not is_within_calling_hours("America/New_York", t, 8, 20)
    assert is_within_calling_hours("America/Los_Angeles", t, 8, 20)


def test_bad_tz_falls_back_to_default():
    noon = datetime(2026, 6, 3, 12, 0, tzinfo=ET)
    assert is_within_calling_hours("Not/AZone", noon, 8, 20, default_tz="America/New_York")


def test_seconds_until_window_zero_when_callable():
    noon = datetime(2026, 6, 3, 12, 0, tzinfo=ET)
    assert seconds_until_window("America/New_York", noon, 8, 20) == 0
