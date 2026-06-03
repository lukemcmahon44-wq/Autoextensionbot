#!/usr/bin/env python3
"""Export leads, calls, and booked appointments to a timestamped Excel workbook."""
from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd

from voiceagent.db.models import Appointment, Call, Lead
from voiceagent.db.session import get_session


def main(argv=None) -> int:
    out = f"report_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    with get_session() as session:
        leads = [
            {
                "id": l.id, "name": l.name, "phone": l.phone, "business_name": l.business_name,
                "email": l.email, "status": l.status.value, "attempts": l.attempts,
                "timezone": l.timezone, "do_not_call": l.do_not_call,
                "last_called_at": l.last_called_at, "source_file": l.source_file,
            }
            for l in session.query(Lead).all()
        ]
        calls = [
            {
                "id": c.id, "lead_id": c.lead_id, "retell_call_id": c.retell_call_id,
                "outcome": c.outcome, "duration_s": c.duration_s, "cost": c.cost,
                "started_at": c.started_at, "ended_at": c.ended_at,
                "disposition_notes": c.disposition_notes,
            }
            for c in session.query(Call).all()
        ]
        appts = [
            {
                "id": a.id, "lead_id": a.lead_id, "scheduled_for": a.scheduled_for,
                "timezone": a.timezone, "confirmed": a.confirmed,
                "calendar_event_id": a.calendar_event_id,
            }
            for a in session.query(Appointment).all()
        ]

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pd.DataFrame(leads or [{}]).to_excel(writer, sheet_name="leads", index=False)
        pd.DataFrame(calls or [{}]).to_excel(writer, sheet_name="calls", index=False)
        pd.DataFrame(appts or [{}]).to_excel(writer, sheet_name="appointments", index=False)

    print(f"Wrote {out}: {len(leads)} leads, {len(calls)} calls, {len(appts)} appointments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
