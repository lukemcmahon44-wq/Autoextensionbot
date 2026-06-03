"""Retry policy. no_answer / voicemail are retryable up to max_attempts with
backoff; bad_number / dnc / booked / not_interested are terminal.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from voiceagent.db.models import Lead, LeadStatus

RETRYABLE = {LeadStatus.no_answer, LeadStatus.voicemail, LeadStatus.retry}

# Maps a provider call-level outcome string to a lead status.
_OUTCOME_TO_STATUS = {
    "no_answer": LeadStatus.no_answer,
    "voicemail": LeadStatus.voicemail,
    "bad_number": LeadStatus.bad_number,
    "completed": None,  # disposition comes from tools/analysis, not the dialer
    "error": LeadStatus.no_answer,
}


def outcome_to_status(outcome: Optional[str]) -> Optional[LeadStatus]:
    return _OUTCOME_TO_STATUS.get(outcome or "", None)


def should_retry(lead: Lead, max_attempts: int) -> bool:
    return lead.status in RETRYABLE and (lead.attempts or 0) < max_attempts


def next_attempt_at(
    attempts: int, backoff_minutes: list[int], base: Optional[datetime] = None
) -> datetime:
    base = base or datetime.utcnow()
    if not backoff_minutes:
        return base
    idx = min(max(attempts - 1, 0), len(backoff_minutes) - 1)
    return base + timedelta(minutes=backoff_minutes[idx])
