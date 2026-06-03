from datetime import datetime

from voiceagent.db.models import Lead, LeadStatus
from voiceagent.orchestration.retry import next_attempt_at, outcome_to_status, should_retry


def test_should_retry_within_attempts():
    lead = Lead(phone="+1", status=LeadStatus.no_answer, attempts=1)
    assert should_retry(lead, 4)
    lead.attempts = 4
    assert not should_retry(lead, 4)


def test_terminal_not_retried():
    lead = Lead(phone="+2", status=LeadStatus.bad_number, attempts=0)
    assert not should_retry(lead, 4)


def test_backoff_progression_and_clamp():
    base = datetime(2026, 1, 1)
    assert (next_attempt_at(1, [30, 120], base) - base).total_seconds() == 1800
    assert (next_attempt_at(2, [30, 120], base) - base).total_seconds() == 7200
    assert (next_attempt_at(9, [30, 120], base) - base).total_seconds() == 7200  # clamped


def test_outcome_to_status():
    assert outcome_to_status("no_answer") == LeadStatus.no_answer
    assert outcome_to_status("voicemail") == LeadStatus.voicemail
    assert outcome_to_status("bad_number") == LeadStatus.bad_number
    assert outcome_to_status("completed") is None
