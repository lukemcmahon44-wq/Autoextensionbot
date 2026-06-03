"""Retell webhook + tool endpoints.

POST /webhooks/retell            -> call_started / call_ended / call_analyzed,
                                    applied idempotently to the DB.
POST /webhooks/retell/tool/{name} -> tool dispatch for retell_managed mode
                                     (Retell's LLM calls our tools here).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from voiceagent.brain.tools import ToolExecutor
from voiceagent.config import get_config, get_settings
from voiceagent.db.models import Call, Lead, LeadStatus, ProcessedWebhookEvent
from voiceagent.db.session import get_session
from voiceagent.observability.logging import for_lead
from voiceagent.orchestration.retry import outcome_to_status
from voiceagent.voice.base import NormalizedCallEvent, VoiceEventType
from voiceagent.voice.factory import get_voice_provider

router = APIRouter(prefix="/webhooks/retell", tags=["webhooks"])

# Short hangups with no disposition are treated as no-answer (retryable);
# longer completed calls with no tool disposition are treated as not_interested.
_SHORT_CALL_SECONDS = 15


def _calendar_or_none():
    settings = get_settings()
    if settings.google_oauth_refresh_token or settings.google_service_account_file:
        try:
            from voiceagent.gcal.google_calendar import GoogleCalendarClient

            return GoogleCalendarClient(settings, get_config().booking.calendar_id)
        except Exception:
            return None
    return None


def _mark_processed(session, event: NormalizedCallEvent) -> bool:
    """Insert the idempotency row. Returns False if already processed."""
    exists = (
        session.query(ProcessedWebhookEvent)
        .filter_by(provider="retell", event_id=event.dedupe_id, event_type=event.event_type.value)
        .first()
    )
    if exists:
        return False
    session.add(
        ProcessedWebhookEvent(
            provider="retell", event_id=event.dedupe_id, event_type=event.event_type.value
        )
    )
    try:
        session.flush()
    except IntegrityError:  # concurrent duplicate delivery
        session.rollback()
        return False
    return True


def _get_or_create_call(session, call_id: str, lead_id: Optional[int]) -> Optional[Call]:
    call = session.query(Call).filter_by(retell_call_id=call_id).first()
    if call is None and lead_id is not None:
        call = Call(lead_id=lead_id, retell_call_id=call_id)
        session.add(call)
        session.flush()
    return call


def _lead_id_from_payload(payload: dict) -> Optional[int]:
    meta = (payload.get("call", {}) or {}).get("metadata", {}) or {}
    raw = meta.get("lead_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _apply(session, event: NormalizedCallEvent, lead_id: Optional[int]) -> None:
    call = _get_or_create_call(session, event.provider_call_id, lead_id)
    lead = session.get(Lead, call.lead_id) if call else (session.get(Lead, lead_id) if lead_id else None)
    log = for_lead(lead.id if lead else "-")

    if event.event_type is VoiceEventType.call_started:
        if call:
            from datetime import datetime

            call.started_at = call.started_at or datetime.utcnow()
        if lead and lead.status not in (LeadStatus.dnc, LeadStatus.booked):
            lead.status = LeadStatus.calling

    elif event.event_type is VoiceEventType.call_ended:
        if call:
            from datetime import datetime

            call.ended_at = datetime.utcnow()
            call.duration_s = event.duration_s
            call.recording_url = event.recording_url
            call.cost = event.cost
            call.cost_breakdown = json.dumps(event.cost_breakdown) if event.cost_breakdown else None
            call.outcome = event.outcome
        # The agent's tools normally set the disposition; fill the gaps here.
        if lead and lead.status == LeadStatus.calling:
            mapped = outcome_to_status(event.outcome)
            if mapped is not None:
                lead.status = mapped
            elif event.outcome == "completed":
                lead.status = (
                    LeadStatus.no_answer
                    if (event.duration_s or 0) < _SHORT_CALL_SECONDS
                    else LeadStatus.not_interested
                )
        log.info("call_ended outcome={} duration={}", event.outcome, event.duration_s)

    elif event.event_type is VoiceEventType.transcript_ready:
        if call:
            call.transcript = event.transcript
            _maybe_analyze(call, lead, log)


def _maybe_analyze(call: Call, lead: Optional[Lead], log) -> None:
    settings = get_settings()
    if not (settings.anthropic_api_key and call.transcript):
        return
    try:
        from voiceagent.brain.claude_engine import ClaudeEngine

        analysis = ClaudeEngine().analyze_transcript(call.transcript)
        call.disposition_notes = analysis.get("summary")
        if lead and lead.status == LeadStatus.calling:
            mapped = {
                "not_interested": LeadStatus.not_interested,
                "callback": LeadStatus.callback,
                "voicemail": LeadStatus.voicemail,
                "no_answer": LeadStatus.no_answer,
            }.get(analysis.get("outcome", ""))
            if mapped:
                lead.status = mapped
    except Exception as exc:  # analysis is best-effort
        log.warning("transcript analysis failed: {}", exc)


@router.post("")
async def retell_webhook(request: Request):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_voice_provider()
    settings = get_settings()

    if settings.retell_api_key and not provider.verify_webhook(headers, raw):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    event = provider.parse_webhook(payload)
    lead_id = _lead_id_from_payload(payload)

    with get_session() as session:
        if not _mark_processed(session, event):
            return {"status": "duplicate", "event": event.event_type.value}
        _apply(session, event, lead_id)
    return {"status": "ok", "event": event.event_type.value}


@router.post("/tool/{tool_name}")
async def retell_tool(tool_name: str, request: Request):
    """Dispatch a Retell-managed tool call to the shared ToolExecutor."""
    body = await request.json()
    args = body.get("args") or body.get("arguments") or {}
    call = body.get("call", {}) or {}
    meta = call.get("metadata", {}) or {}
    try:
        lead_id = int(meta.get("lead_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="missing lead_id in call metadata")

    with get_session() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(status_code=404, detail="lead not found")
        executor = ToolExecutor(
            session, lead, get_config(), calendar=_calendar_or_none(), logger=for_lead(lead_id)
        )
        result = executor.execute(tool_name, args)
    return result
