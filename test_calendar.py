#!/usr/bin/env python3
"""Standalone test: Google Calendar.

Reads availability for the next few days, then creates AND deletes a throwaway
event so you can confirm read + write both work without leaving residue.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from voiceagent.config import get_config, get_settings


def main() -> int:
    s = get_settings()
    c = get_config()
    if not (s.google_oauth_refresh_token or s.google_service_account_file):
        print("FAIL: set GOOGLE_OAUTH_REFRESH_TOKEN (+ client id/secret) or GOOGLE_SERVICE_ACCOUNT_FILE")
        return 1
    try:
        from voiceagent.gcal.google_calendar import GoogleCalendarClient

        cal = GoogleCalendarClient(s, c.booking.calendar_id)
        info = cal.validate()
        print(f"calendar: {info.get('summary')} ({info.get('timeZone')})")

        now = datetime.now()
        slots = cal.find_open_slots(
            start=now,
            end=now + timedelta(days=c.booking.search_days_ahead),
            duration_min=c.booking.appointment_minutes,
            business_hours_start=c.booking.business_hours_start,
            business_hours_end=c.booking.business_hours_end,
            tz=c.booking.your_timezone,
        )
        print(f"open slots found: {len(slots)} (showing up to 3)")
        for sl in slots[:3]:
            print(f"  {sl['label']}")
        if not slots:
            print("FAIL: no open slots returned (check business hours / calendar)")
            return 1

        start = datetime.fromisoformat(slots[0]["start_iso"])
        event = cal.create_event(
            start=start,
            duration_min=c.booking.appointment_minutes,
            summary="[TEST] voice agent throwaway — safe to ignore",
            description="Created by test_calendar.py; deleted immediately.",
            tz=c.booking.your_timezone,
        )
        print(f"created event {event['event_id']}")
        cal.delete_event(event["event_id"])
        print("deleted event")
        print("\nPASS: Google Calendar read + write")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
