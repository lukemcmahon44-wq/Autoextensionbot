#!/usr/bin/env python3
"""Standalone test: ElevenLabs TTS. Generates a sample audio file you can play."""
from __future__ import annotations

import sys

from voiceagent.config import get_config, get_settings

OUT = "sample_voice.mp3"


def main() -> int:
    s = get_settings()
    c = get_config()
    if not s.elevenlabs_api_key:
        print("FAIL: ELEVENLABS_API_KEY is not set")
        return 1
    try:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=s.elevenlabs_api_key)
        audio = client.text_to_speech.convert(
            voice_id=c.elevenlabs.voice_id,
            model_id=c.elevenlabs.model,
            text="Hi! This is a quick test of the voice sales agent. If you can hear me clearly, the text to speech integration is working.",
        )
        data = b"".join(audio) if hasattr(audio, "__iter__") and not isinstance(audio, (bytes, bytearray)) else audio
        with open(OUT, "wb") as f:
            f.write(data)
        print(f"PASS: wrote {OUT} ({len(data)} bytes) with voice_id={c.elevenlabs.voice_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
