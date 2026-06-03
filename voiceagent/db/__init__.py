"""Database layer: SQLAlchemy models, session factory, schema reference.

Alembic migrations under ``/alembic`` are authoritative for real deployments;
``init_db()`` (create_all) is a dev convenience for SQLite.
"""
from voiceagent.db.models import (  # noqa: F401
    Appointment,
    Base,
    Call,
    Callback,
    ConsentLog,
    Lead,
    LeadStatus,
    ProcessedWebhookEvent,
)
