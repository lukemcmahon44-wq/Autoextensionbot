"""Claude tool-calling engine.

Two responsibilities:
  * ``run_turn`` — drive one conversational turn in ``custom_claude`` mode:
    call Claude with tools, run the tool-use loop server-side, return the text
    the agent should speak.
  * ``analyze_transcript`` — offline disposition/summary used in ``retell_managed``
    mode (where Retell's LLM ran the live call).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from voiceagent.brain.tools import TOOL_DEFINITIONS
from voiceagent.config import AppConfig, Settings, get_config, get_settings

_CLASSIFY_TOOL = {
    "name": "classify_call",
    "description": "Record the outcome and a one-line summary of a sales call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["booked", "not_interested", "callback", "no_answer", "voicemail", "bad_number", "completed"],
            },
            "summary": {"type": "string"},
            "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        },
        "required": ["outcome", "summary"],
    },
}


class ClaudeEngine:
    def __init__(self, settings: Optional[Settings] = None, config: Optional[AppConfig] = None):
        self.settings = settings or get_settings()
        self.config = config or get_config()
        from anthropic import Anthropic  # lazy import

        self.client = Anthropic(api_key=self.settings.anthropic_api_key)

    def run_turn(
        self,
        system: str,
        messages: list[dict[str, Any]],
        executor=None,
        tools: Optional[list[dict[str, Any]]] = None,
        max_iters: int = 6,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Run one turn to completion (resolving any tool calls).

        Returns (assistant_text_to_speak, updated_messages, tool_log).
        ``messages`` is mutated in place (and also returned) so the caller can
        persist the running conversation across turns.
        """
        tools = tools if tools is not None else TOOL_DEFINITIONS
        tool_log: list[dict[str, Any]] = []

        for _ in range(max_iters):
            resp = self.client.messages.create(
                model=self.config.claude.model,
                max_tokens=self.config.claude.max_tokens,
                temperature=self.config.claude.temperature,
                system=system,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if resp.stop_reason == "tool_use" and tool_uses:
                results = []
                for tu in tool_uses:
                    out = (
                        executor.execute(tu.name, tu.input)
                        if executor is not None
                        else {"status": "error", "message": "no executor configured"}
                    )
                    tool_log.append({"name": tu.name, "input": tu.input, "result": out})
                    results.append(
                        {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out)}
                    )
                messages.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return text, messages, tool_log

        return "", messages, tool_log

    def analyze_transcript(self, transcript: str) -> dict[str, Any]:
        """Offline classification of a finished call (retell_managed mode)."""
        resp = self.client.messages.create(
            model=self.config.claude.model,
            max_tokens=400,
            system="You analyze outbound sales call transcripts. Use the classify_call tool.",
            tools=[_CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_call"},
            messages=[{"role": "user", "content": (transcript or "")[:12000]}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "classify_call":
                return dict(block.input)
        return {"outcome": "completed", "summary": "(no analysis)"}
