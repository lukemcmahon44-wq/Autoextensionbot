from voiceagent.api.llm_ws import LiveCallSession, _transcript_to_messages
from voiceagent.config import get_config


class FakeEngine:
    def __init__(self):
        self.config = get_config()

    def run_turn(self, system, messages, executor=None, tools=None, max_iters=6):
        return ("Hi, this is Alex with a quick question.", messages, [])


def test_transcript_to_messages_primes_and_merges():
    msgs = _transcript_to_messages(
        [
            {"role": "agent", "content": "Hello"},
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "who's this"},
        ]
    )
    assert msgs[0]["role"] == "user"  # primed so Anthropic gets a user-first sequence
    assert msgs[-1]["role"] == "user"
    # consecutive same-role turns merged
    assert any(m["role"] == "user" and "who's this" in m["content"] for m in msgs)


def test_call_details_sets_lead_id():
    s = LiveCallSession("c1", engine=FakeEngine())
    s.handle({"interaction_type": "call_details", "call": {"metadata": {"lead_id": "7"}}})
    assert s.lead_id == 7


def test_response_required_without_lead_returns_response():
    s = LiveCallSession("c1", engine=FakeEngine())
    r = s.handle(
        {"interaction_type": "response_required", "response_id": 3, "transcript": [{"role": "user", "content": "hello"}]}
    )
    assert r["response_type"] == "response"
    assert r["response_id"] == 3
    assert r["content"]


def test_ping_pong_echoes_timestamp():
    s = LiveCallSession("c1", engine=FakeEngine())
    assert s.handle({"interaction_type": "ping_pong", "timestamp": 123})["timestamp"] == 123


def test_update_only_needs_no_reply():
    s = LiveCallSession("c1", engine=FakeEngine())
    assert s.handle({"interaction_type": "update_only", "transcript": []}) is None
