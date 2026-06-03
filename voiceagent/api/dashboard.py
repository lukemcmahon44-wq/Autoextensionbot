"""Single-page dashboard + JSON stats endpoint.

GET /            -> the dashboard HTML (auto-refreshes via fetch).
GET /api/stats   -> live numbers: leads by status, calls today, bookings,
                    in-progress, recent transcripts, total + per-component cost.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func

from voiceagent.db.models import Appointment, Call, Lead, LeadStatus
from voiceagent.db.session import get_session
from voiceagent.observability.costs import cost_by_component, total_cost

router = APIRouter(tags=["dashboard"])
_HTML = (Path(__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")


@router.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _HTML


@router.get("/api/stats")
def stats() -> JSONResponse:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as session:
        by_status = {s.value: 0 for s in LeadStatus}
        for status, count in session.query(Lead.status, func.count()).group_by(Lead.status).all():
            key = status.value if hasattr(status, "value") else str(status)
            by_status[key] = count

        calls_today = (
            session.query(Call).filter(Call.started_at.isnot(None), Call.started_at >= midnight.replace(tzinfo=None)).count()
        )
        bookings = session.query(Appointment).count()
        in_progress = by_status.get("calling", 0)

        recent = (
            session.query(Call).order_by(Call.id.desc()).limit(10).all()
        )
        recent_out = []
        for c in recent:
            lead = session.get(Lead, c.lead_id)
            snippet = (c.transcript or "")[:240]
            recent_out.append(
                {
                    "lead": (lead.name if lead else None) or (lead.phone if lead else "?"),
                    "outcome": c.outcome,
                    "duration_s": c.duration_s,
                    "cost": c.cost,
                    "transcript": snippet,
                }
            )

        payload = {
            "leads_by_status": by_status,
            "calls_today": calls_today,
            "bookings": bookings,
            "in_progress": in_progress,
            "total_leads": sum(by_status.values()),
            "total_cost": round(total_cost(session), 4),
            "cost_by_component": {k: round(v, 4) for k, v in cost_by_component(session).items()},
            "recent": recent_out,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    return JSONResponse(payload)
