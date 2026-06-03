import pytest

from voiceagent.config import get_config
from voiceagent.prompts.agent_prompt import build_system_prompt
from voiceagent.voice.base import LeadContext, VoiceEventType
from voiceagent.voice.factory import get_voice_provider
from voiceagent.voice.retell_provider import RetellProvider
from voiceagent.voice.vapi_provider import VapiProvider


def test_parse_call_ended_voicemail_cost_duration():
    p = RetellProvider()
    ev = p.parse_webhook(
        {
            "event": "call_ended",
            "call": {
                "call_id": "c1",
                "disconnection_reason": "voicemail_reached",
                "start_timestamp": 1000,
                "end_timestamp": 4000,
                "transcript": "hi",
                "call_cost": {"combined_cost": 250, "product_costs": [{"product": "tts", "cost": 100}]},
            },
        }
    )
    assert ev.event_type is VoiceEventType.call_ended
    assert ev.outcome == "voicemail"
    assert ev.duration_s == 3
    assert round(ev.cost, 2) == 2.50
    assert ev.dedupe_id == "call_ended:c1"


def test_parse_call_started_and_analyzed():
    p = RetellProvider()
    assert p.parse_webhook({"event": "call_started", "call": {"call_id": "c2"}}).event_type is VoiceEventType.call_started
    assert p.parse_webhook({"event": "call_analyzed", "call": {"call_id": "c3"}}).event_type is VoiceEventType.transcript_ready


def test_verify_webhook_without_signature_is_false():
    assert RetellProvider().verify_webhook({}, b"{}") is False


def test_factory_returns_retell():
    assert isinstance(get_voice_provider(), RetellProvider)


def test_vapi_stub_raises():
    with pytest.raises(NotImplementedError):
        VapiProvider().place_call(LeadContext(lead_id=1, phone="+1"), "+1")


def test_prompt_includes_disclosure_and_lead_when_present():
    s = build_system_prompt(get_config(), LeadContext(lead_id=1, phone="+1", name="Sam", business_name="Acme"))
    assert "AI assistant" in s and "Sam" in s
    assert "is_decision_maker" in s  # qualifying criteria injected


def test_prompt_keeps_placeholders_without_lead_and_can_disable_disclosure():
    cfg = get_config().model_copy(deep=True)
    cfg.compliance.disclose_ai_identity = False
    s = build_system_prompt(cfg, None)
    assert "{{lead_name}}" in s
    assert "AI assistant" not in s
