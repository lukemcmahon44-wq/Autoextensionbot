"""VoiceProvider interface — abstracts Retell (primary) from Vapi (swap-in).

Call orchestration talks ONLY to this interface, never to a vendor SDK
directly, so swapping providers never touches call logic (Section 1).

The interface deliberately spans BOTH llm modes:
  * retell_managed -> Retell's own LLM runs the conversation; our webhooks
    service tool calls. ``build_agent_payload`` describes the agent to create.
  * custom_claude  -> Claude drives every turn over the LLM websocket; the
    provider still places the call and normalizes webhooks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class VoiceEventType(str, Enum):
    call_started = "call_started"
    call_ended = "call_ended"
    transcript_ready = "transcript_ready"
    unknown = "unknown"


@dataclass
class LeadContext:
    """Minimal lead view handed to the provider when placing a call."""

    lead_id: int
    phone: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    timezone: Optional[str] = None
    notes: Optional[str] = None


@dataclass
class OutboundCallResult:
    provider_call_id: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedCallEvent:
    """Vendor-agnostic view of a webhook event for idempotent DB updates."""

    event_type: VoiceEventType
    provider_call_id: str
    dedupe_id: str  # unique per delivery; written to processed_webhook_events
    outcome: Optional[str] = None
    duration_s: Optional[int] = None
    transcript: Optional[str] = None
    recording_url: Optional[str] = None
    cost: Optional[float] = None
    cost_breakdown: Optional[dict[str, Any]] = None
    raw: dict[str, Any] = field(default_factory=dict)


class VoiceProvider(ABC):
    """Abstract outbound-voice provider (Retell, Vapi, ...)."""

    name: str = "base"

    @abstractmethod
    def place_call(
        self,
        lead: LeadContext,
        from_number: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> OutboundCallResult:
        """Place an outbound call; return a provider call handle."""

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], raw_body: bytes) -> bool:
        """Verify webhook authenticity (signature / shared secret)."""

    @abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> NormalizedCallEvent:
        """Normalize a provider webhook payload into a NormalizedCallEvent."""

    @abstractmethod
    def build_agent_payload(self) -> dict[str, Any]:
        """Return the provider-specific agent/LLM config (voice, prompt, tools)."""
