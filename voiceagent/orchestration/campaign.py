"""Campaign runner: ingest leads, then place calls respecting the concurrency
cap, calling hours (lead-local), DNC, and retry state.

Out-of-window leads are simply not selected this pass — they stay ``new``/
``retry`` and are picked up by a later pass (queued, not skipped).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from voiceagent.compliance.consent import log_consent
from voiceagent.config import AppConfig, Settings, get_config, get_settings
from voiceagent.db.models import Call, Lead, LeadStatus
from voiceagent.db.session import get_session
from voiceagent.ingest.excel import ingest_file, persist, write_rejected
from voiceagent.observability.logging import configure_logging, for_lead
from voiceagent.orchestration.calling_hours import is_within_calling_hours
from voiceagent.voice.base import LeadContext
from voiceagent.voice.factory import get_voice_provider

_SELECTABLE = [LeadStatus.new, LeadStatus.retry]


class CampaignRunner:
    def __init__(self, provider=None, settings: Optional[Settings] = None, config: Optional[AppConfig] = None):
        self.settings = settings or get_settings()
        self.config = config or get_config()
        self._provider = provider  # lazy: don't construct SDK client unless needed
        configure_logging()

    @property
    def provider(self):
        if self._provider is None:
            self._provider = get_voice_provider(self.config, self.settings)
        return self._provider

    # -- ingest ------------------------------------------------------------- #
    def ingest_and_load(self, path: str) -> dict:
        result = ingest_file(path, default_timezone=self.config.calling.default_timezone)
        write_rejected(result.rejected)
        with get_session() as session:
            counts = persist(result, session)
        print(result.summary())
        return {"loaded": len(result.leads), "rejected": len(result.rejected),
                "duplicates_merged": result.duplicates_merged, **counts}

    # -- selection ---------------------------------------------------------- #
    def eligible_leads(self, session, now: Optional[datetime] = None) -> list[Lead]:
        candidates = (
            session.query(Lead)
            .filter(Lead.status.in_(_SELECTABLE), Lead.do_not_call.is_(False))
            .order_by(Lead.last_called_at.is_(None).desc(), Lead.last_called_at.asc())
            .all()
        )
        c = self.config.calling
        return [
            lead
            for lead in candidates
            if lead.attempts < c.max_attempts
            and is_within_calling_hours(
                lead.timezone, now, c.calling_hours_start, c.calling_hours_end, c.default_timezone
            )
        ]

    def in_flight_count(self, session) -> int:
        return session.query(Lead).filter(Lead.status == LeadStatus.calling).count()

    # -- placement ---------------------------------------------------------- #
    def place_call(self, session, lead: Lead):
        log = for_lead(lead.id)
        ctx = LeadContext(
            lead_id=lead.id, phone=lead.phone, name=lead.name,
            business_name=lead.business_name, timezone=lead.timezone, notes=lead.notes,
        )
        result = self.provider.place_call(ctx, self.settings.twilio_from_number)
        session.add(Call(lead_id=lead.id, retell_call_id=result.provider_call_id,
                         started_at=datetime.utcnow(), outcome="initiated"))
        lead.status = LeadStatus.calling
        lead.attempts = (lead.attempts or 0) + 1
        lead.last_called_at = datetime.utcnow()
        log_consent(session, lead.id, "call_initiated", f"provider_call_id={result.provider_call_id}")
        if self.config.compliance.disclose_ai_identity:
            log_consent(session, lead.id, "ai_disclosure_required", "disclose_ai_identity=true")
        log.info("placed call {} to {}", result.provider_call_id, lead.phone)
        return result

    def run_once(self, max_calls: Optional[int] = None, now: Optional[datetime] = None) -> dict:
        placed, errors = 0, 0
        with get_session() as session:
            cap = self.config.calling.concurrency_cap
            budget = max(0, cap - self.in_flight_count(session))
            if max_calls is not None:
                budget = min(budget, max_calls)
            eligible = self.eligible_leads(session, now=now)[:budget]
            for lead in eligible:
                try:
                    self.place_call(session, lead)
                    placed += 1
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    for_lead(lead.id).error("failed to place call: {}", exc)
        return {"placed": placed, "errors": errors, "budget": budget}
