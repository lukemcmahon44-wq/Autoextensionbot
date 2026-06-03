"""Engine + session factory + dev bootstrap.

SQLite enforces foreign keys only when ``PRAGMA foreign_keys=ON`` is set per
connection, so we wire that up via an event listener.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from voiceagent.config import get_settings
from voiceagent.db.models import Base

_settings = get_settings()
_is_sqlite = _settings.database_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine: Engine = create_engine(
    _settings.database_url, connect_args=_connect_args, future=True
)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


@event.listens_for(Engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _record):  # pragma: no cover - tiny glue
    if _is_sqlite:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def init_db() -> None:
    """Create all tables. Dev convenience; Alembic is authoritative for prod."""
    Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Transactional session scope: commit on success, rollback on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
