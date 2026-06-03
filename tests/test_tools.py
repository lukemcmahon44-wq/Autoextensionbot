import pytest

from voiceagent.brain.tools import ToolExecutor
from voiceagent.config import get_config
from voiceagent.db.models import Appointment, Callback, ConsentLog, Lead, LeadStatus


class FakeCalendar:
    def find_open_slots(self, **kwargs):
        return [{"start_iso": "2099-01-01T10:00:00", "end_iso": "2099-01-01T10:30:00", "label": "Tue 10:00 AM"}]

    def create_event(self, **kwargs):
        return {"event_id": "evt_1", "html_link": "http://cal/evt_1"}

    def delete_event(self, event_id):
        pass


@pytest.fixture
def lead(session):
    l = Lead(name="Tess", phone="+14155552671", email="t@x.com", status=LeadStatus.calling)
    session.add(l)
    session.flush()
    return l


def _ex(session, lead):
    return ToolExecutor(session, lead, get_config(), calendar=FakeCalendar())


def test_check_availability(session, lead):
    r = _ex(session, lead).execute("check_availability", {})
    assert r["status"] == "ok" and r["count"] >= 1


def test_book_requires_verbal_confirmation(session, lead):
    r = _ex(session, lead).execute("book_appointment", {"slot_start": "2099-01-01T10:00:00", "confirmed_verbally": False})
    assert r["status"] == "needs_confirmation"
    assert session.query(Appointment).count() == 0


def test_book_success_sets_status_and_logs(session, lead):
    r = _ex(session, lead).execute("book_appointment", {"slot_start": "2099-01-01T10:00:00", "confirmed_verbally": True})
    assert r["status"] == "booked" and r["event_id"] == "evt_1"
    assert lead.status == LeadStatus.booked
    assert session.query(Appointment).filter_by(lead_id=lead.id).count() == 1
    assert session.query(ConsentLog).filter_by(lead_id=lead.id, event_type="disposition").count() >= 1


def test_mark_callback(session, lead):
    r = _ex(session, lead).execute("mark_callback", {"requested_time": "2099-01-02T15:00:00", "notes": "later"})
    assert r["status"] == "callback_set"
    assert session.query(Callback).filter_by(lead_id=lead.id).count() == 1
    assert lead.status == LeadStatus.callback


def test_flag_dnc(session, lead):
    r = _ex(session, lead).execute("flag_dnc", {"reason": "asked to stop"})
    assert r["status"] == "dnc_set"
    assert lead.do_not_call is True and lead.status == LeadStatus.dnc


def test_end_call_sets_status(session, lead):
    _ex(session, lead).execute("end_call", {"outcome": "not_interested"})
    assert lead.status == LeadStatus.not_interested


def test_end_call_never_downgrades_booking(session, lead):
    lead.status = LeadStatus.booked
    _ex(session, lead).execute("end_call", {"outcome": "completed"})
    assert lead.status == LeadStatus.booked


def test_unknown_tool(session, lead):
    assert _ex(session, lead).execute("nope", {})["status"] == "error"
