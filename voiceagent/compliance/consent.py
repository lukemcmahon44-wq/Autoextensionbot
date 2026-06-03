"""Compliance helpers: consent audit trail + DNC enforcement.

Every disposition and consent-relevant event is written to ``consent_log`` so
there is an auditable trail (Section 6).
"""
from __future__ import annotations

from typing import Optional

from voiceagent.db.models import ConsentLog, Lead, LeadStatus

# Statuses that mean "never auto-dial again".
TERMINAL = {LeadStatus.dnc, LeadStatus.booked, LeadStatus.not_interested, LeadStatus.bad_number}


def log_consent(session, lead_id: int, event_type: str, detail: Optional[str] = None) -> ConsentLog:
    entry = ConsentLog(lead_id=lead_id, event_type=event_type, detail=detail)
    session.add(entry)
    session.flush()
    return entry


def set_dnc(session, lead_id: int, reason: str) -> Optional[Lead]:
    """Honor an opt-out: flag do_not_call, set status=dnc, and log both events."""
    lead = session.get(Lead, lead_id)
    if lead is not None:
        lead.do_not_call = True
        lead.status = LeadStatus.dnc
    log_consent(session, lead_id, "opt_out_requested", reason)
    log_consent(session, lead_id, "dnc_set", reason)
    return lead


def is_dnc(lead: Lead) -> bool:
    return bool(lead.do_not_call) or lead.status == LeadStatus.dnc


def is_callable(lead: Lead) -> bool:
    """True if the lead may be auto-dialed (not opted out, not terminal)."""
    return (not is_dnc(lead)) and lead.status not in TERMINAL
