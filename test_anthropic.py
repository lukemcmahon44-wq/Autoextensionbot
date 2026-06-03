#!/usr/bin/env python3
"""Standalone test: Claude (Anthropic) tool-calling works.

Drives one turn through ClaudeEngine with a fake executor and confirms the
model actually invoked the check_availability tool. PASS/FAIL with the error.
"""
from __future__ import annotations

import sys

from voiceagent.config import get_settings


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, name, tool_input):
        self.calls.append(name)
        if name == "check_availability":
            return {"status": "ok", "slots": [{"label": "Tuesday at 10:00 AM", "start_iso": "2099-01-01T10:00:00"}]}
        return {"status": "ok"}


def main() -> int:
    if not get_settings().anthropic_api_key:
        print("FAIL: ANTHROPIC_API_KEY is not set")
        return 1
    try:
        from voiceagent.brain.claude_engine import ClaudeEngine

        engine = ClaudeEngine()
        executor = _FakeExecutor()
        system = (
            "You are a scheduling assistant. You MUST call the check_availability tool "
            "before offering any time, then offer one of the returned slots."
        )
        messages = [{"role": "user", "content": "Can we set up a quick call? What's open?"}]
        text, _msgs, tool_log = engine.run_turn(system, messages, executor=executor)

        used = [t["name"] for t in tool_log]
        print(f"model: {engine.config.claude.model}")
        print(f"tools invoked: {used}")
        print(f"assistant said: {text[:200]}")

        ok = "check_availability" in used
        print(f"\n{'PASS' if ok else 'FAIL'}: Anthropic tool-calling")
        return 0 if ok else 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
