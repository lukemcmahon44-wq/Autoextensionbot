#!/usr/bin/env python3
"""Standalone test: place ONE live Retell call to a number you specify and run
the full agent against you, so you can role-play a lead.

Usage:
    python test_retell.py +15551234567

Requires RETELL_API_KEY, RETELL_AGENT_ID, and TWILIO_FROM_NUMBER. Answer your
phone and talk to the agent; the call disposition lands in the DB via webhooks
(run the FastAPI app + expose PUBLIC_BASE_URL to capture those).
"""
from __future__ import annotations

import argparse
import sys
import time

from voiceagent.config import get_config, get_settings
from voiceagent.db.models import Lead
from voiceagent.db.session import get_session, init_db
from voiceagent.ingest.phone import normalize_phone
from voiceagent.voice.base import LeadContext
from voiceagent.voice.factory import get_voice_provider


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Place one live Retell test call.")
    parser.add_argument("number", help="Number to call (your own phone), any format")
    parser.add_argument("--poll", type=int, default=60, help="Seconds to poll call status")
    args = parser.parse_args(argv)

    s, c = get_settings(), get_config()
    missing = [k for k, v in {
        "RETELL_API_KEY": s.retell_api_key,
        "RETELL_AGENT_ID": s.retell_agent_id,
        "TWILIO_FROM_NUMBER": s.twilio_from_number,
    }.items() if not v]
    if missing:
        print(f"FAIL: missing {', '.join(missing)}")
        return 1

    phone = normalize_phone(args.number)
    if not phone.ok:
        print(f"FAIL: bad number: {phone.reason}")
        return 1

    try:
        init_db()
        with get_session() as session:
            lead = session.query(Lead).filter_by(phone=phone.e164).first()
            if lead is None:
                lead = Lead(name="Role-play Lead", phone=phone.e164, business_name="Test Co")
                session.add(lead)
                session.flush()
            lead_id = lead.id
            ctx = LeadContext(lead_id=lead_id, phone=phone.e164, name=lead.name, business_name=lead.business_name)

        provider = get_voice_provider(c, s)
        print(f"placing call to {phone.e164} from {s.twilio_from_number} (llm_mode={c.llm_mode})...")
        result = provider.place_call(ctx, s.twilio_from_number, metadata={"test": "true"})
        print(f"call placed: {result.provider_call_id} (status={result.status})")
        print("Answer your phone and role-play a lead!")

        # Poll status (best-effort; needs Retell client)
        client = getattr(provider, "client", None)
        deadline = time.time() + args.poll
        last = None
        while client is not None and time.time() < deadline:
            time.sleep(5)
            try:
                call = client.call.retrieve(result.provider_call_id)
                status = getattr(call, "call_status", None)
                if status != last:
                    print(f"  status: {status}")
                    last = status
                if status in ("ended", "error"):
                    break
            except Exception:
                break
        print("\nPASS: call placed (verify the conversation + check the dashboard for disposition)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
