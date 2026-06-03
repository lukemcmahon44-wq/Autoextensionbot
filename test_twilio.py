#!/usr/bin/env python3
"""Standalone test: Twilio credentials + lists your phone numbers."""
from __future__ import annotations

import sys

from voiceagent.config import get_settings


def main() -> int:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        print("FAIL: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")
        return 1
    try:
        from voiceagent.telephony.twilio_client import TwilioClient

        client = TwilioClient(s)
        info = client.validate()
        numbers = client.list_numbers()
        print(f"account: {info.get('friendly_name')} ({info.get('status')})")
        print(f"numbers ({len(numbers)}):")
        for n in numbers:
            print(f"  {n['phone_number']}  {n['friendly_name']}")
        if s.twilio_from_number and not any(n["phone_number"] == s.twilio_from_number for n in numbers):
            print(f"  NOTE: TWILIO_FROM_NUMBER {s.twilio_from_number} not found in this account")
        print("\nPASS: Twilio credentials valid")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
