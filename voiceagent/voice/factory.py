"""Provider factory — the single place call logic resolves a VoiceProvider."""
from __future__ import annotations

from typing import Optional

from voiceagent.config import AppConfig, Settings, get_config, get_settings
from voiceagent.voice.base import VoiceProvider


def get_voice_provider(
    config: Optional[AppConfig] = None, settings: Optional[Settings] = None
) -> VoiceProvider:
    config = config or get_config()
    settings = settings or get_settings()
    if config.voice_provider == "retell":
        from voiceagent.voice.retell_provider import RetellProvider

        return RetellProvider(settings, config)
    if config.voice_provider == "vapi":
        from voiceagent.voice.vapi_provider import VapiProvider

        return VapiProvider(settings, config)
    raise ValueError(f"Unknown voice_provider: {config.voice_provider!r}")
