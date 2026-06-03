"""Test config: isolated temp SQLite DB, no live API keys.

Env is set BEFORE importing voiceagent so the cached settings + engine use the
throwaway database. Tables are cleaned between tests.
"""
from __future__ import annotations

import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["RETELL_API_KEY"] = ""  # disables webhook signature enforcement in tests

import pytest  # noqa: E402

from voiceagent.db.models import Base  # noqa: E402
from voiceagent.db.session import SessionLocal, engine, init_db  # noqa: E402

init_db()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
