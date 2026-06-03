"""custom_claude live brain: Retell custom-LLM WebSocket.

Retell connects per call; for each ``response_required`` it sends the running
transcript and we reply with the text the agent should speak. Claude drives the
turn and executes tools (calendar/DB) server-side.

The per-turn logic lives in ``LiveCallSession`` so it can be unit-tested with a
fake engine, independent of any live socket.
"""
from __future__ import annotations

from typing import Any, Optional

from voiceagent.config import get_config, get_settings
from voiceagent.db.models import Lead
from voiceagent.db.session import get_session
from voiceagent.observability.logging import for_lead
from voiceagent.prompts.agent_prompt import build_system_prompt
from voiceagent.voice.base import LeadContext

CONFIG_FRAME = {"response_type": "config", "config": {"auto_reconnect": True, "call_details": True}}


def _transcript_to_messages(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Retell transcript turns to Anthropic messages, merging consecutive
    same-role turns (Anthropic requires alternating roles)."""
    messages: list[dict[str, Any]] = []
    for turn in transcript or []:
        role = "assistant" if turn.get("role") == "agent" else "user"
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += f" {content}"
        else:
            messages.append({"role": role, "content": content})
    # A turn must start with a user message for Anthropic; prime if needed.
    if not messages or messages[0]["role"] != "user":
        messages.insert(0, {"role": "user", "content": "(call connected)"})
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": "(continue)"})
    return messages


class LiveCallSession:
    def __init__(self, call_id: str, engine=None, settings=None, config=None):
        self.call_id = call_id
        self.settings = settings or get_settings()
        self.config = config or get_config()
        self.lead_id: Optional[int] = None
        self._engine = engine

    @property
    def engine(self):
        if self._engine is None:
            from voiceagent.brain.claude_engine import ClaudeEngine

            self._engine = ClaudeEngine(self.settings, self.config)
        return self._engine

    def on_call_details(self, frame: dict[str, Any]) -> None:
        meta = (frame.get("call", {}) or {}).get("metadata", {}) or {}
        try:
            self.lead_id = int(meta.get("lead_id"))
        except (TypeError, ValueError):
            self.lead_id = None

    def _calendar(self):
        if self.settings.google_oauth_refresh_token or self.settings.google_service_account_file:
            try:
                from voiceagent.gcal.google_calendar import GoogleCalendarClient

                return GoogleCalendarClient(self.settings, self.config.booking.calendar_id)
            except Exception:
                return None
        return None

    def handle(self, frame: dict[str, Any]) -> Optional[dict[str, Any]]:
        itype = frame.get("interaction_type")
        if itype == "call_details":
            self.on_call_details(frame)
            return None
        if itype == "ping_pong":
            return {"response_type": "ping_pong", "timestamp": frame.get("timestamp")}
        if itype not in ("response_required", "reminder_required"):
            return None  # update_only etc. need no reply

        response_id = frame.get("response_id")
        messages = _transcript_to_messages(frame.get("transcript", []))

        # No lead resolved -> converse without DB/booking side effects.
        if self.lead_id is None:
            text, *_ = self.engine.run_turn(
                build_system_prompt(self.config, None), messages, executor=None
            )
            return self._response(response_id, text)

        from voiceagent.brain.tools import ToolExecutor

        with get_session() as session:
            lead = session.get(Lead, self.lead_id)
            lead_ctx = (
                LeadContext(
                    lead_id=lead.id, phone=lead.phone, name=lead.name,
                    business_name=lead.business_name, timezone=lead.timezone, notes=lead.notes,
                )
                if lead
                else None
            )
            system = build_system_prompt(self.config, lead_ctx)
            executor = (
                ToolExecutor(session, lead, self.config, calendar=self._calendar(),
                             logger=for_lead(self.lead_id))
                if lead
                else None
            )
            text, _msgs, _tools = self.engine.run_turn(system, messages, executor=executor)
        return self._response(response_id, text)

    @staticmethod
    def _response(response_id, content: str) -> dict[str, Any]:
        return {
            "response_type": "response",
            "response_id": response_id,
            "content": content or "Sorry, could you say that again?",
            "content_complete": True,
            "end_call": False,
        }


def register_ws(app) -> None:
    """Attach the custom-LLM websocket route to a FastAPI app."""
    from fastapi import WebSocket, WebSocketDisconnect

    @app.websocket("/llm-websocket/{call_id}")
    async def llm_websocket(websocket: WebSocket, call_id: str):  # pragma: no cover - needs live socket
        await websocket.accept()
        session = LiveCallSession(call_id)
        log = for_lead("-")
        await websocket.send_json(CONFIG_FRAME)
        try:
            while True:
                frame = await websocket.receive_json()
                reply = session.handle(frame)
                if reply is not None:
                    await websocket.send_json(reply)
        except WebSocketDisconnect:
            log.info("llm websocket disconnected for call {}", call_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("llm websocket error: {}", exc)
            await websocket.close()
