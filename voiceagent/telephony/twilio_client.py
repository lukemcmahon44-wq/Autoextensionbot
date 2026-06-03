"""Twilio helper: validate credentials, list numbers, provision a number.

Outbound calling itself goes through Retell (which dials via Twilio); this
module is for credential checks and number management.
"""
from __future__ import annotations

from typing import Optional

from voiceagent.config import Settings, get_settings


class TwilioClient:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        from twilio.rest import Client  # lazy import

        self._client = Client(self.settings.twilio_account_sid, self.settings.twilio_auth_token)

    def validate(self) -> dict:
        """Fetch the account — raises if credentials are invalid."""
        acct = self._client.api.v2010.accounts(self.settings.twilio_account_sid).fetch()
        return {"friendly_name": acct.friendly_name, "status": acct.status, "type": acct.type}

    def list_numbers(self) -> list[dict]:
        nums = self._client.incoming_phone_numbers.list(limit=100)
        return [
            {
                "phone_number": n.phone_number,
                "friendly_name": n.friendly_name,
                "sid": n.sid,
                "voice_url": n.voice_url,
            }
            for n in nums
        ]

    def search_available(self, area_code: Optional[int] = None, country: str = "US") -> list[str]:
        avail = self._client.available_phone_numbers(country).local.list(
            area_code=area_code, voice_enabled=True, limit=20
        )
        return [a.phone_number for a in avail]

    def provision_number(self, phone_number: str) -> dict:
        n = self._client.incoming_phone_numbers.create(phone_number=phone_number)
        return {"phone_number": n.phone_number, "sid": n.sid}
