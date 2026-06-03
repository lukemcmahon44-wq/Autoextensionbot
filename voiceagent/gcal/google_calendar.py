"""Google Calendar: read availability (free/busy) + create/delete events.

Auth precedence: OAuth user refresh token (it's *your* calendar) -> service
account file. Slots are generated within business hours (weekdays) in your
timezone and filtered against the calendar's busy intervals.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from voiceagent.config import Settings, get_settings

_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class GoogleCalendarClient:
    def __init__(self, settings: Optional[Settings] = None, calendar_id: Optional[str] = None):
        self.settings = settings or get_settings()
        self.calendar_id = calendar_id or self.settings.google_calendar_id or "primary"
        self.service = self._build_service()

    # -- auth --------------------------------------------------------------- #
    def _credentials(self):
        s = self.settings
        if s.google_oauth_refresh_token:
            from google.oauth2.credentials import Credentials

            return Credentials(
                token=None,
                refresh_token=s.google_oauth_refresh_token,
                client_id=s.google_oauth_client_id,
                client_secret=s.google_oauth_client_secret,
                token_uri=_TOKEN_URI,
                scopes=_SCOPES,
            )
        if s.google_service_account_file:
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(
                s.google_service_account_file, scopes=_SCOPES
            )
        raise RuntimeError(
            "No Google credentials. Set GOOGLE_OAUTH_REFRESH_TOKEN (+ client id/secret) "
            "or GOOGLE_SERVICE_ACCOUNT_FILE."
        )

    def _build_service(self):
        from googleapiclient.discovery import build

        return build("calendar", "v3", credentials=self._credentials(), cache_discovery=False)

    # -- validation (healthcheck) ------------------------------------------ #
    def validate(self) -> dict:
        cal = self.service.calendars().get(calendarId=self.calendar_id).execute()
        return {"id": cal.get("id"), "summary": cal.get("summary"), "timeZone": cal.get("timeZone")}

    # -- availability ------------------------------------------------------- #
    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "items": [{"id": self.calendar_id}],
        }
        resp = self.service.freebusy().query(body=body).execute()
        busy = resp["calendars"][self.calendar_id].get("busy", [])
        return [(_parse_rfc3339(b["start"]), _parse_rfc3339(b["end"])) for b in busy]

    def find_open_slots(
        self,
        start: datetime,
        end: datetime,
        duration_min: int,
        business_hours_start: int,
        business_hours_end: int,
        tz: str,
        weekdays_only: bool = True,
    ) -> list[dict]:
        zone = ZoneInfo(tz)
        now = datetime.now(zone)
        start = start.astimezone(zone) if start.tzinfo else start.replace(tzinfo=zone)
        end = end.astimezone(zone) if end.tzinfo else end.replace(tzinfo=zone)

        busy = self.busy_intervals(start, end)

        def overlaps_busy(s: datetime, e: datetime) -> bool:
            return any(s < b_end and e > b_start for b_start, b_end in busy)

        slots: list[dict] = []
        day = start.date()
        last_day = end.date()
        step = timedelta(minutes=duration_min)
        while day <= last_day:
            if not (weekdays_only and day.weekday() >= 5):  # 5,6 = Sat,Sun
                cursor = datetime(day.year, day.month, day.day, business_hours_start, tzinfo=zone)
                day_end = datetime(day.year, day.month, day.day, business_hours_end, tzinfo=zone)
                while cursor + step <= day_end:
                    slot_end = cursor + step
                    if cursor > now and not overlaps_busy(cursor, slot_end):
                        slots.append(
                            {
                                "start_iso": cursor.isoformat(),
                                "end_iso": slot_end.isoformat(),
                                "label": cursor.strftime("%A %B %d at %I:%M %p %Z"),
                            }
                        )
                    cursor += step
            day += timedelta(days=1)
        return slots

    # -- booking ------------------------------------------------------------ #
    def create_event(
        self,
        start: datetime,
        duration_min: int,
        summary: str,
        description: str = "",
        attendee_email: Optional[str] = None,
        tz: str = "America/New_York",
    ) -> dict:
        zone = ZoneInfo(tz)
        if start.tzinfo is None:
            start = start.replace(tzinfo=zone)
        end = start + timedelta(minutes=duration_min)
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }
        if attendee_email:
            body["attendees"] = [{"email": attendee_email}]
        event = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
        return {"event_id": event.get("id"), "html_link": event.get("htmlLink")}

    def delete_event(self, event_id: str) -> None:
        self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
