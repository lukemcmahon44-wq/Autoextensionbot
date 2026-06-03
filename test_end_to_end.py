#!/usr/bin/env python3
"""Full dry run: ingest sample sheet -> place one call to your own number ->
(you role-play and book) -> verify the appointment + calendar event.

Usage:
    python test_end_to_end.py --to +15551234567

Stages print PASS/FAIL. Run the FastAPI app with a public PUBLIC_BASE_URL so
booking tool calls + webhooks reach this process during the call.
"""
from __future__ import annotations

import argparse
import sys
import time

from voiceagent.config import get_config, get_settings
from voiceagent.db.models import Appointment, Lead
from voiceagent.db.session import get_session, init_db
from voiceagent.health import check_anthropic, check_calendar, check_retell
from voiceagent.ingest.phone import normalize_phone
from voiceagent.ingest.sample import make_sample
from voiceagent.orchestration.campaign import CampaignRunner
from voiceagent.voice.base import LeadContext
from voiceagent.voice.factory import get_voice_provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end live test.")
    parser.add_argument("--to", required=True, help="Your own phone number to call")
    parser.add_argument("--wait", type=int, default=180, help="Seconds to wait for booking")
    args = parser.parse_args(argv)

    s, c = get_settings(), get_config()
    print("== preflight ==")
    for chk in (check_retell(), check_anthropic(), check_calendar()):
        print(f"  [{'PASS' if chk.ok else 'FAIL'}] {chk.name}: {chk.detail}")
    phone = normalize_phone(args.to)
    if not phone.ok:
        print(f"FAIL: bad --to number: {phone.reason}")
        return 1

    print("\n== stage 1: ingest sample ==")
    init_db()
    sample = make_sample("sample_leads.xlsx")
    runner = CampaignRunner(settings=s, config=c)
    summary = runner.ingest_and_load(str(sample))
    print(f"  ingest: {summary}")

    print("\n== stage 2: place call to your number ==")
    with get_session() as session:
        lead = session.query(Lead).filter_by(phone=phone.e164).first()
        if lead is None:
            lead = Lead(name="E2E Tester", phone=phone.e164, business_name="Test Co")
            session.add(lead)
            session.flush()
        lead_id = lead.id
        ctx = LeadContext(lead_id=lead_id, phone=phone.e164, name=lead.name, business_name=lead.business_name)
    provider = get_voice_provider(c, s)
    result = provider.place_call(ctx, s.twilio_from_number, metadata={"e2e": "true"})
    print(f"  call placed: {result.provider_call_id}")
    print("  >> Answer and book an appointment with the agent. <<")

    print("\n== stage 3: wait for a booking ==")
    booked = None
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(8)
        with get_session() as session:
            appt = session.query(Appointment).filter_by(lead_id=lead_id).order_by(Appointment.id.desc()).first()
            if appt:
                booked = {"event_id": appt.calendar_event_id, "when": appt.scheduled_for.isoformat()}
                break
    if not booked:
        print("FAIL: no appointment recorded within wait window")
        return 1
    print(f"  appointment recorded: {booked}")

    print("\n== stage 4: verify event on calendar ==")
    try:
        from voiceagent.gcal.google_calendar import GoogleCalendarClient

        cal = GoogleCalendarClient(s, c.booking.calendar_id)
        ev = cal.service.events().get(calendarId=cal.calendar_id, eventId=booked["event_id"]).execute()
        print(f"  calendar event found: {ev.get('summary')} @ {ev.get('start')}")
        print("\nPASS: end-to-end (ingest -> call -> book -> calendar)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not verify calendar event: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
