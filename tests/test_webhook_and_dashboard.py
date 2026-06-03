import json

from fastapi.testclient import TestClient

from voiceagent.api.app import app
from voiceagent.db.models import Call, Lead, LeadStatus, ProcessedWebhookEvent
from voiceagent.db.session import SessionLocal

client = TestClient(app)


def _seed_lead() -> int:
    s = SessionLocal()
    lead = Lead(name="Webhook Lead", phone="+14155559999", status=LeadStatus.new)
    s.add(lead)
    s.commit()
    lead_id = lead.id
    s.close()
    return lead_id


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_dashboard_html_served():
    r = client.get("/")
    assert r.status_code == 200 and "Voice Sales Agent" in r.text


def test_stats_shape():
    d = client.get("/api/stats").json()
    for key in ("leads_by_status", "calls_today", "bookings", "total_cost", "recent"):
        assert key in d


def test_webhook_idempotent_and_outcome_applied():
    lead_id = _seed_lead()
    started = {"event": "call_started", "call": {"call_id": "wh1", "metadata": {"lead_id": str(lead_id)}}}

    assert client.post("/webhooks/retell", content=json.dumps(started)).json()["status"] == "ok"
    # duplicate delivery is a no-op
    assert client.post("/webhooks/retell", content=json.dumps(started)).json()["status"] == "duplicate"

    ended = {
        "event": "call_ended",
        "call": {
            "call_id": "wh1",
            "metadata": {"lead_id": str(lead_id)},
            "disconnection_reason": "dial_no_answer",
            "start_timestamp": 1000,
            "end_timestamp": 2000,
        },
    }
    client.post("/webhooks/retell", content=json.dumps(ended))

    s = SessionLocal()
    lead = s.get(Lead, lead_id)
    call = s.query(Call).filter_by(retell_call_id="wh1").first()
    assert lead.status == LeadStatus.no_answer
    assert call is not None and call.outcome == "no_answer"
    assert s.query(ProcessedWebhookEvent).filter_by(event_id="call_started:wh1").count() == 1
    s.close()


def test_tool_endpoint_books(monkeypatch):
    lead_id = _seed_lead()

    class FakeCal:
        def find_open_slots(self, **kw):
            return [{"start_iso": "2099-01-01T10:00:00", "label": "Tue"}]

        def create_event(self, **kw):
            return {"event_id": "evt_x", "html_link": "http://x"}

    import voiceagent.webhooks.retell_webhooks as wh

    monkeypatch.setattr(wh, "_calendar_or_none", lambda: FakeCal())
    body = {
        "name": "book_appointment",
        "args": {"slot_start": "2099-01-01T10:00:00", "confirmed_verbally": True},
        "call": {"metadata": {"lead_id": str(lead_id)}},
    }
    r = client.post("/webhooks/retell/tool/book_appointment", content=json.dumps(body))
    assert r.json()["status"] == "booked"
