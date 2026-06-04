#!/usr/bin/env python3
"""Create the Retell LLM + agent from your config, so you don't hand-configure
the dashboard. Wires the prompt, tools, voice, webhook, and (for custom_claude)
the LLM websocket.

    python provision_retell.py --dry-run     # print payloads, call nothing
    python provision_retell.py               # create the agent, print its id

After it runs, paste the printed RETELL_AGENT_ID (and, for custom_claude,
RETELL_LLM_WEBSOCKET_URL) into .env. Requires PUBLIC_BASE_URL and a voice_id.
"""
from __future__ import annotations

import argparse
import json
import sys

from voiceagent.brain.tools import to_retell_tools
from voiceagent.config import get_config, get_settings
from voiceagent.prompts.agent_prompt import build_system_prompt


def _urls(settings):
    base = settings.public_base_url.rstrip("/") or "<PUBLIC_BASE_URL>"
    webhook_url = f"{base}/webhooks/retell"
    tool_endpoint = f"{base}/webhooks/retell/tool"
    ws_url = settings.retell_llm_websocket_url or (
        base.replace("https://", "wss://").replace("http://", "ws://") + "/llm-websocket"
    )
    return base, webhook_url, tool_endpoint, ws_url


def build_llm_payload(settings, config, tool_endpoint) -> dict:
    esc = config.escalation
    payload = {
        "model": config.retell.managed_model,
        "model_temperature": config.claude.temperature,
        "general_prompt": build_system_prompt(config, None),
        "general_tools": to_retell_tools(
            tool_endpoint,
            transfer_number=esc.transfer_number if esc.enabled else None,
            transfer_type=esc.transfer_type,
        ),
        "start_speaker": config.retell.start_speaker,
        "default_dynamic_variables": {
            "lead_name": "there",
            "business_name": "",
            "lead_notes": "",
            "lead_timezone": config.booking.your_timezone,
        },
    }
    if config.script.opening_line:
        payload["begin_message"] = config.script.opening_line
    return payload


def build_agent_payload(settings, config, response_engine, webhook_url) -> dict:
    payload = {
        "response_engine": response_engine,
        "voice_id": config.retell.voice_id,
        "agent_name": f"{config.compliance.company_name} outbound",
        "enable_backchannel": config.retell.enable_backchannel,
        "interruption_sensitivity": config.retell.interruption_sensitivity,
        "responsiveness": config.retell.responsiveness,
        "webhook_url": webhook_url,
        "timezone": config.booking.your_timezone,
    }
    if config.retell.ambient_sound:
        payload["ambient_sound"] = config.retell.ambient_sound
    if config.script.voicemail_message:
        payload["voicemail_message"] = config.script.voicemail_message
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Provision a Retell agent from config.")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads, call no API")
    args = parser.parse_args(argv)

    s, c = get_settings(), get_config()
    base, webhook_url, tool_endpoint, ws_url = _urls(s)
    llm_payload = build_llm_payload(s, c, tool_endpoint)

    if args.dry_run:
        print(f"llm_mode = {c.llm_mode}")
        print(f"webhook_url = {webhook_url}")
        print(f"llm_websocket_url = {ws_url}  (custom_claude)")
        if c.llm_mode == "retell_managed":
            print("\n-- LLM payload (general_prompt truncated) --")
            preview = dict(llm_payload)
            preview["general_prompt"] = preview["general_prompt"][:200] + "..."
            print(json.dumps(preview, indent=2)[:2000])
            print("\n-- tools --")
            print(json.dumps([{"type": t["type"], "name": t["name"]} for t in llm_payload["general_tools"]], indent=2))
        engine = (
            {"type": "retell-llm", "llm_id": "<created>"}
            if c.llm_mode == "retell_managed"
            else {"type": "custom-llm", "llm_websocket_url": ws_url}
        )
        print("\n-- agent payload --")
        print(json.dumps(build_agent_payload(s, c, engine, webhook_url), indent=2))
        return 0

    # Live provisioning
    if not s.retell_api_key:
        print("FAIL: RETELL_API_KEY not set")
        return 1
    if not c.retell.voice_id:
        print("FAIL: set retell.voice_id in config.yaml")
        return 1
    if base.startswith("<"):
        print("FAIL: set PUBLIC_BASE_URL in .env")
        return 1
    try:
        from retell import Retell

        client = Retell(api_key=s.retell_api_key)
        if c.llm_mode == "retell_managed":
            llm = client.llm.create(**llm_payload)
            llm_id = getattr(llm, "llm_id", None)
            engine = {"type": "retell-llm", "llm_id": llm_id}
            print(f"created LLM: {llm_id}")
        else:
            engine = {"type": "custom-llm", "llm_websocket_url": ws_url}

        agent = client.agent.create(**build_agent_payload(s, c, engine, webhook_url))
        agent_id = getattr(agent, "agent_id", None)
        print(f"created agent: {agent_id}")
        print("\nAdd to .env:")
        print(f"RETELL_AGENT_ID={agent_id}")
        if c.llm_mode == "custom_claude":
            print(f"RETELL_LLM_WEBSOCKET_URL={ws_url}")
        print("\nNext: in Retell, attach your Twilio number to this agent.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
