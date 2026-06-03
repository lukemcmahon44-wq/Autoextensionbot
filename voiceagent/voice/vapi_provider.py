"""Vapi provider stub.

Demonstrates the swap-in seam: implement these four methods against the Vapi
SDK and set ``voice_provider: vapi`` in config.yaml — no call logic changes.
"""
from __future__ import annotations

from typing import Any, Optional

from voiceagent.config import AppConfig, Settings, get_config, get_settings
from voiceagent.voice.base import (
    LeadContext,
    NormalizedCallEvent,
    OutboundCallResult,
    VoiceProvider,
)

_MSG = "Vapi provider is a stub. Implement against the Vapi SDK or use voice_provider: retell."


class VapiProvider(VoiceProvider):
    name = "vapi"

    def __init__(self, settings: Optional[Settings] = None, config: Optional[AppConfig] = None):
        self.settings = settings or get_settings()
        self.config = config or get_config()

    def place_call(self, lead: LeadContext, from_number: str,
                   metadata: Optional[dict[str, Any]] = None) -> OutboundCallResult:
        raise NotImplementedError(_MSG)

    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        raise NotImplementedError(_MSG)

    def parse_webhook(self, payload: dict[str, Any]) -> NormalizedCallEvent:
        raise NotImplementedError(_MSG)

    def build_agent_payload(self) -> dict[str, Any]:
        raise NotImplementedError(_MSG)
