"""FastAPI application factory.

Mounts: Retell webhooks + tool dispatch, the dashboard, a health endpoint, and
(when using custom_claude) the LLM WebSocket. Run with:

    uvicorn voiceagent.api.app:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from voiceagent.api.dashboard import router as dashboard_router
from voiceagent.api.llm_ws import register_ws
from voiceagent.config import get_config
from voiceagent.observability.logging import configure_logging
from voiceagent.webhooks.retell_webhooks import router as retell_router


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Voice Sales Agent", version="0.1.0")

    app.include_router(retell_router)
    app.include_router(dashboard_router)
    register_ws(app)  # /llm-websocket/{call_id} (used in custom_claude mode)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict:
        cfg = get_config()
        return {"status": "ok", "llm_mode": cfg.llm_mode, "voice_provider": cfg.voice_provider}

    return app


app = create_app()
