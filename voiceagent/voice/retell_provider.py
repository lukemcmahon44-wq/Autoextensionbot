"""Retell implementation of the VoiceProvider interface.

Retell owns the audio path (Twilio telephony, STT, turn-taking, barge-in,
ElevenLabs TTS). This adapter places outbound calls, verifies + normalizes
webhooks, and describes the agent config for provisioning.
"""
from __future__ import annotations

from typing import Any, Optional

from retell import Retell
from retell.lib import verify as retell_verify

from voiceagent.config import AppConfig, Settings, get_config, get_settings
from voiceagent.voice.base import (
    LeadContext,
    NormalizedCallEvent,
    OutboundCallResult,
    VoiceEventType,
    VoiceProvider,
)

# Retell disconnection_reason -> our call-level outcome vocabulary.
# Conversational outcomes (booked/not_interested/callback/dnc) come from the
# agent's tool calls, not from here; the webhook handler reconciles the two.
_DISCONNECT_OUTCOME = {
    "user_hangup": "completed",
    "agent_hangup": "completed",
    "call_transfer": "completed",
    "inactivity": "no_answer",
    "max_duration_reached": "completed",
    "dial_busy": "no_answer",
    "dial_failed": "bad_number",
    "dial_no_answer": "no_answer",
    "voicemail_reached": "voicemail",
    "machine_detected": "voicemail",
    "error_llm_websocket_open": "error",
    "error_no_audio_received": "no_answer",
    "registered_call_timeout": "no_answer",
}


class RetellProvider(VoiceProvider):
    name = "retell"

    def __init__(self, settings: Optional[Settings] = None, config: Optional[AppConfig] = None):
        self.settings = settings or get_settings()
        self.config = config or get_config()
        self.client = Retell(api_key=self.settings.retell_api_key)

    # ------------------------------------------------------------------ #
    def place_call(
        self,
        lead: LeadContext,
        from_number: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> OutboundCallResult:
        meta = {"lead_id": str(lead.lead_id)}
        if metadata:
            meta.update({k: str(v) for k, v in metadata.items()})
        dynamic_vars = {
            "lead_name": lead.name or "there",
            "business_name": lead.business_name or "",
            "lead_timezone": lead.timezone or "",
        }
        call = self.client.call.create_phone_call(
            from_number=from_number,
            to_number=lead.phone,
            override_agent_id=self.settings.retell_agent_id or None,
            metadata=meta,
            retell_llm_dynamic_variables=dynamic_vars,
        )
        raw = call.model_dump() if hasattr(call, "model_dump") else dict(call)
        return OutboundCallResult(
            provider_call_id=getattr(call, "call_id", "") or raw.get("call_id", ""),
            status=getattr(call, "call_status", "registered") or "registered",
            raw=raw,
        )

    # ------------------------------------------------------------------ #
    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        # Header lookup is case-insensitive in practice; check both.
        sig = headers.get("x-retell-signature") or headers.get("X-Retell-Signature")
        if not sig:
            return False
        body = raw_body.decode("utf-8") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)
        try:
            return bool(retell_verify(body, self.settings.retell_api_key, sig))
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    def parse_webhook(self, payload: dict[str, Any]) -> NormalizedCallEvent:
        event = payload.get("event", "")
        call = payload.get("call", payload) or {}
        call_id = call.get("call_id") or payload.get("call_id") or ""

        etype = {
            "call_started": VoiceEventType.call_started,
            "call_ended": VoiceEventType.call_ended,
            "call_analyzed": VoiceEventType.transcript_ready,
        }.get(event, VoiceEventType.unknown)

        duration_s = None
        start = call.get("start_timestamp")
        end = call.get("end_timestamp")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
            duration_s = int((end - start) / 1000)

        analysis = call.get("call_analysis") or {}
        outcome = None
        if etype is VoiceEventType.call_ended:
            reason = call.get("disconnection_reason", "")
            outcome = _DISCONNECT_OUTCOME.get(reason, "completed")
            if analysis.get("in_voicemail"):
                outcome = "voicemail"

        cost = None
        cost_breakdown = None
        call_cost = call.get("call_cost") or {}
        if call_cost:
            combined = call_cost.get("combined_cost")
            if combined is not None:
                cost = float(combined) / 100.0  # Retell reports cents
            cost_breakdown = call_cost

        return NormalizedCallEvent(
            event_type=etype,
            provider_call_id=call_id,
            dedupe_id=f"{event}:{call_id}",
            outcome=outcome,
            duration_s=duration_s,
            transcript=call.get("transcript"),
            recording_url=call.get("recording_url"),
            cost=cost,
            cost_breakdown=cost_breakdown,
            raw=payload,
        )

    # ------------------------------------------------------------------ #
    def build_agent_payload(self) -> dict[str, Any]:
        """Describe the agent config (used by provisioning / documentation).

        For ``custom_claude`` the LLM is our websocket; for ``retell_managed``
        the prompt + tools are configured on the Retell LLM. Tools point at our
        public tool endpoints.
        """
        from voiceagent.brain.tools import to_retell_tools

        base = self.settings.public_base_url.rstrip("/") or "<PUBLIC_BASE_URL>"
        tool_endpoint = f"{base}/webhooks/retell/tool"
        return {
            "voice_id": self.config.retell.voice_id,
            "enable_backchannel": self.config.retell.enable_backchannel,
            "interruption_sensitivity": self.config.retell.interruption_sensitivity,
            "responsiveness": self.config.retell.responsiveness,
            "ambient_sound": self.config.retell.ambient_sound,
            "llm_mode": self.config.llm_mode,
            "llm_websocket_url": self.settings.retell_llm_websocket_url or f"{base}/llm-websocket",
            "webhook_url": f"{base}/webhooks/retell",
            "tools": to_retell_tools(tool_endpoint),
        }
