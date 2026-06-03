"""Agent tools: Anthropic tool schemas + a ToolExecutor that runs them against
the calendar and the DB. Shared by both llm modes:

  * custom_claude  -> Claude calls these via the Anthropic tool-use loop.
  * retell_managed -> Retell calls our tool webhook, which dispatches here.

Tools (Section 4): check_availability, book_appointment, mark_callback,
flag_dnc, end_call.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from voiceagent.compliance.consent import log_consent, set_dnc
from voiceagent.db.models import Appointment, Callback, Lead, LeadStatus

# --------------------------------------------------------------------------- #
# Tool schemas (Anthropic "tools" format)                                     #
# --------------------------------------------------------------------------- #
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "check_availability",
        "description": "Get real open appointment slots from the calendar. Call this "
        "before offering any times. Returns a list of bookable slots.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days from today to search (default uses config).",
                }
            },
        },
    },
    {
        "name": "book_appointment",
        "description": "Create the appointment on the calendar. You MUST have said the "
        "exact day/date/time back to the lead and gotten a clear yes; only then set "
        "confirmed_verbally=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slot_start": {"type": "string", "description": "Start time, ISO-8601 (from check_availability)."},
                "confirmed_verbally": {"type": "boolean", "description": "True only after the lead verbally agreed to this exact time."},
                "attendee_notes": {"type": "string", "description": "Optional notes for the meeting."},
            },
            "required": ["slot_start", "confirmed_verbally"],
        },
    },
    {
        "name": "mark_callback",
        "description": "Record that the lead wants to be called back at a specific time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "requested_time": {"type": "string", "description": "Requested callback time, ISO-8601."},
                "notes": {"type": "string"},
            },
            "required": ["requested_time"],
        },
    },
    {
        "name": "flag_dnc",
        "description": "The lead asked not to be called again / opted out. Mark do-not-call. "
        "Call immediately on any opt-out; the lead will never be called again.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "end_call",
        "description": "End the call with the correct outcome.",
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["booked", "not_interested", "callback", "no_answer", "voicemail", "bad_number", "completed"],
                },
                "notes": {"type": "string"},
            },
            "required": ["outcome"],
        },
    },
]

_OUTCOME_TO_STATUS = {
    "not_interested": LeadStatus.not_interested,
    "callback": LeadStatus.callback,
    "no_answer": LeadStatus.no_answer,
    "voicemail": LeadStatus.voicemail,
    "bad_number": LeadStatus.bad_number,
    "booked": LeadStatus.booked,
}


def to_retell_tools(tool_endpoint: str) -> list[dict[str, Any]]:
    """Convert the schemas to Retell custom-function format (managed mode).

    Each function posts to a single dispatch endpoint with the tool name.
    """
    out = []
    for t in TOOL_DEFINITIONS:
        out.append(
            {
                "type": "custom",
                "name": t["name"],
                "description": t["description"],
                "url": f"{tool_endpoint.rstrip('/')}/{t['name']}",
                "parameters": t["input_schema"],
                "speak_during_execution": t["name"] == "check_availability",
                "speak_after_execution": True,
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Executor                                                                     #
# --------------------------------------------------------------------------- #
class ToolExecutor:
    """Runs a tool call against the calendar + DB for a single lead/call.

    ``calendar`` must expose ``find_open_slots(start, end, duration_min,
    business_hours_start, business_hours_end, tz)`` and
    ``create_event(start, duration_min, summary, description, attendee_email, tz)``.
    It may be None for flows that don't book (tools that need it will say so).
    """

    def __init__(self, session, lead: Lead, config, calendar=None, logger=None):
        self.session = session
        self.lead = lead
        self.config = config
        self.calendar = calendar
        self.log = logger

    def execute(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "check_availability": self._check_availability,
            "book_appointment": self._book_appointment,
            "mark_callback": self._mark_callback,
            "flag_dnc": self._flag_dnc,
            "end_call": self._end_call,
        }.get(name)
        if handler is None:
            return {"status": "error", "message": f"unknown tool {name!r}"}
        try:
            result = handler(tool_input or {})
        except Exception as exc:  # surface to the model so it can recover gracefully
            if self.log:
                self.log.exception("tool {} failed", name)
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        if self.log:
            self.log.info("tool {} -> {}", name, result.get("status"))
        return result

    # -- individual tools --------------------------------------------------- #
    def _booking_window(self, days_ahead: Optional[int]):
        b = self.config.booking
        days = days_ahead or b.search_days_ahead
        start = datetime.now()
        end = start + timedelta(days=days)
        return start, end, b

    def _check_availability(self, args):
        if self.calendar is None:
            return {"status": "error", "message": "calendar not configured"}
        start, end, b = self._booking_window(args.get("days_ahead"))
        slots = self.calendar.find_open_slots(
            start=start,
            end=end,
            duration_min=b.appointment_minutes,
            business_hours_start=b.business_hours_start,
            business_hours_end=b.business_hours_end,
            tz=b.your_timezone,
        )
        return {"status": "ok", "slots": slots[:8], "count": len(slots)}

    def _book_appointment(self, args):
        if not args.get("confirmed_verbally"):
            return {
                "status": "needs_confirmation",
                "message": "Say the exact day, date and time back to the lead and get a clear "
                "yes first, then call again with confirmed_verbally=true.",
            }
        if self.calendar is None:
            return {"status": "error", "message": "calendar not configured"}
        b = self.config.booking
        start_dt = datetime.fromisoformat(args["slot_start"])
        summary = f"{self.config.script.product_name} intro — {self.lead.name or self.lead.phone}"
        desc = f"Booked by {self.config.compliance.agent_name} (AI). Notes: {args.get('attendee_notes', '')}"
        event = self.calendar.create_event(
            start=start_dt,
            duration_min=b.appointment_minutes,
            summary=summary,
            description=desc,
            attendee_email=self.lead.email,
            tz=b.your_timezone,
        )
        appt = Appointment(
            lead_id=self.lead.id,
            calendar_event_id=event.get("event_id"),
            scheduled_for=start_dt,
            timezone=b.your_timezone,
            confirmed=True,
        )
        self.session.add(appt)
        self.lead.status = LeadStatus.booked
        log_consent(self.session, self.lead.id, "disposition", f"booked {start_dt.isoformat()}")
        self.session.flush()
        return {
            "status": "booked",
            "scheduled_for": start_dt.isoformat(),
            "timezone": b.your_timezone,
            "event_id": event.get("event_id"),
            "link": event.get("html_link"),
            "reconfirm": f"Booked for {start_dt.strftime('%A %B %d at %I:%M %p')}. "
            "Re-confirm this time with the lead and tell them an invite is on the way.",
        }

    def _mark_callback(self, args):
        when = datetime.fromisoformat(args["requested_time"])
        self.session.add(
            Callback(lead_id=self.lead.id, requested_for=when, notes=args.get("notes"))
        )
        if self.lead.status != LeadStatus.booked:
            self.lead.status = LeadStatus.callback
        log_consent(self.session, self.lead.id, "disposition", f"callback {when.isoformat()}")
        self.session.flush()
        return {"status": "callback_set", "requested_for": when.isoformat()}

    def _flag_dnc(self, args):
        set_dnc(self.session, self.lead.id, args.get("reason", "verbal opt-out"))
        self.session.flush()
        return {"status": "dnc_set", "message": "Lead will not be called again."}

    def _end_call(self, args):
        outcome = args.get("outcome", "completed")
        new_status = _OUTCOME_TO_STATUS.get(outcome)
        # Never downgrade a booking or a DNC.
        if self.lead.status not in (LeadStatus.booked, LeadStatus.dnc) and new_status:
            self.lead.status = new_status
        log_consent(self.session, self.lead.id, "disposition", f"end_call outcome={outcome}")
        self.session.flush()
        return {"status": "ended", "outcome": outcome}
