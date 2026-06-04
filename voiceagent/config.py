"""Typed configuration for the voice sales agent.

Two layers, both cached:
  * ``Settings``  -> secrets / environment values from ``.env`` (see ``.env.example``).
  * ``AppConfig`` -> tunable behavior from ``config.yaml`` (script, hours, criteria...).

Use ``get_settings()`` and ``get_config()`` anywhere; nothing else reads the
environment or the YAML directly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


# --------------------------------------------------------------------------- #
# Secrets / environment (.env)                                                #
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Secrets + environment. Never commit real values; see ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Anthropic (Claude)
    anthropic_api_key: str = ""

    # Retell
    retell_api_key: str = ""
    retell_agent_id: str = ""              # used when llm_mode=retell_managed
    retell_llm_websocket_url: str = ""     # used when llm_mode=custom_claude

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""           # E.164 outbound caller ID, wired to Retell

    # ElevenLabs
    elevenlabs_api_key: str = ""

    # Google Calendar (OAuth user flow is primary; service account optional)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_refresh_token: str = ""
    google_service_account_file: str = ""
    google_calendar_id: str = "primary"

    # Server / webhooks
    public_base_url: str = ""              # public https base for Retell webhooks
    webhook_secret: str = ""               # shared secret / signature verification
    database_url: str = "sqlite:///voiceagent.db"

    # App
    log_level: str = "INFO"
    env: str = "dev"


# --------------------------------------------------------------------------- #
# Tunable behavior (config.yaml)                                              #
# --------------------------------------------------------------------------- #
class QualifyingCriterion(BaseModel):
    key: str
    question: str
    required: bool = False


class RetellVoiceConfig(BaseModel):
    voice_id: str = ""                     # ElevenLabs voice id on the Retell agent
    managed_model: str = "gpt-4o"          # Retell LLM model for retell_managed mode
    start_speaker: str = "agent"           # who speaks first on outbound
    enable_backchannel: bool = True
    interruption_sensitivity: float = 0.8  # barge-in
    responsiveness: float = 1.0
    ambient_sound: Optional[str] = None


class EscalationConfig(BaseModel):
    """Human handoff. transfer_number must be E.164. cold_transfer hands off
    immediately; warm_transfer announces the lead first (Retell-managed mode)."""

    enabled: bool = False
    transfer_number: str = ""
    transfer_type: Literal["cold_transfer", "warm_transfer"] = "cold_transfer"


class ElevenLabsConfig(BaseModel):
    voice_id: str = "EXAVITQu4vr4xnSDxMaL"  # default sample voice; set your own
    model: str = "eleven_turbo_v2_5"


class ClaudeConfig(BaseModel):
    model: str = "claude-sonnet-4-5"
    max_tokens: int = 1024
    temperature: float = 0.7


class CallingConfig(BaseModel):
    concurrency_cap: int = 5
    calling_hours_start: int = 8           # 08:00 in the LEAD's local tz
    calling_hours_end: int = 20            # 20:00 in the LEAD's local tz
    max_attempts: int = 4
    backoff_minutes: list[int] = Field(default_factory=lambda: [30, 120, 360, 1440])
    default_timezone: str = "America/New_York"  # fallback if area-code inference fails


class BookingConfig(BaseModel):
    calendar_id: str = "primary"
    appointment_minutes: int = 30
    buffer_minutes: int = 15
    business_hours_start: int = 9          # availability offered, in your tz
    business_hours_end: int = 17
    search_days_ahead: int = 7
    your_timezone: str = "America/New_York"


class ComplianceConfig(BaseModel):
    disclose_ai_identity: bool = True      # several US states mandate this
    company_name: str = "Your Company"
    agent_name: str = "Alex"


class ScriptConfig(BaseModel):
    product_name: str = "Your Product"
    one_liner: str = "we help <ICP> achieve <outcome> without <pain>"
    primary_pain_points: list[str] = Field(default_factory=list)
    call_objective: str = "book a 30-minute intro call"
    opening_line: str = ""           # optional first line; blank = let the model open
    voicemail_message: str = ""      # left on voicemail detection; blank = just hang up


class AppConfig(BaseModel):
    voice_provider: Literal["retell", "vapi"] = "retell"
    llm_mode: Literal["retell_managed", "custom_claude"] = "retell_managed"
    retell: RetellVoiceConfig = Field(default_factory=RetellVoiceConfig)
    elevenlabs: ElevenLabsConfig = Field(default_factory=ElevenLabsConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    calling: CallingConfig = Field(default_factory=CallingConfig)
    booking: BookingConfig = Field(default_factory=BookingConfig)
    qualifying_criteria: list[QualifyingCriterion] = Field(default_factory=list)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    script: ScriptConfig = Field(default_factory=ScriptConfig)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config(path: Optional[str] = None) -> AppConfig:
    cfg_path = Path(path) if path else CONFIG_PATH
    if not cfg_path.exists():
        return AppConfig()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig(**data)
