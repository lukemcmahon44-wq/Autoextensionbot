"""voiceagent — production-grade outbound voice AI sales agent.

Stack: Retell (voice, abstracted) · Claude / Retell-managed LLM (brain) ·
ElevenLabs (TTS) · Twilio (telephony) · Google Calendar (booking) ·
FastAPI (webhooks + dashboard) · SQLite→Postgres (SQLAlchemy + Alembic).
"""

__version__ = "0.1.0"
