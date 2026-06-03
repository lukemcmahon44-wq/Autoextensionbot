"""SQLAlchemy 2.x models. Runs on SQLite now; migrates cleanly to Postgres.

Portability notes:
  * Enums use ``native_enum=False`` -> portable ``VARCHAR + CHECK`` on both backends
    (avoids Postgres ENUM type churn in migrations).
  * ``created_at`` uses ``server_default=func.now()`` (CURRENT_TIMESTAMP on SQLite,
    now() on Postgres). Booleans default app-side for portability.
  * ``leads.phone`` is UNIQUE  -> de-dupe key.
  * ``calls.retell_call_id`` is UNIQUE and ``processed_webhook_events`` enforces
    idempotency for duplicate webhook deliveries (Section 5).
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class LeadStatus(str, enum.Enum):
    new = "new"
    calling = "calling"
    retry = "retry"
    booked = "booked"
    not_interested = "not_interested"
    callback = "callback"
    no_answer = "no_answer"
    voicemail = "voicemail"
    bad_number = "bad_number"
    dnc = "dnc"


# Terminal statuses are never re-queued by the retry engine.
TERMINAL_STATUSES = {
    LeadStatus.booked,
    LeadStatus.not_interested,
    LeadStatus.bad_number,
    LeadStatus.dnc,
}
# Statuses that the retry engine re-queues (subject to max_attempts).
RETRYABLE_STATUSES = {LeadStatus.no_answer, LeadStatus.voicemail, LeadStatus.retry}


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    business_name: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(320))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    timezone: Mapped[Optional[str]] = mapped_column(String(64))
    source_file: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, native_enum=False, length=20),
        default=LeadStatus.new,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_called_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    calls = relationship("Call", back_populates="lead", cascade="all, delete-orphan")
    appointments = relationship(
        "Appointment", back_populates="lead", cascade="all, delete-orphan"
    )
    callbacks = relationship(
        "Callback", back_populates="lead", cascade="all, delete-orphan"
    )
    consent_events = relationship(
        "ConsentLog", back_populates="lead", cascade="all, delete-orphan"
    )


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id"), nullable=False, index=True
    )
    retell_call_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_s: Mapped[Optional[int]] = mapped_column(Integer)
    outcome: Mapped[Optional[str]] = mapped_column(String(32))
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    recording_url: Mapped[Optional[str]] = mapped_column(String(1024))
    cost: Mapped[Optional[float]] = mapped_column(Float)
    cost_breakdown: Mapped[Optional[str]] = mapped_column(Text)  # JSON: retell/twilio/11labs
    disposition_notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    lead = relationship("Lead", back_populates="calls")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id"), nullable=False, index=True
    )
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(256))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    lead = relationship("Lead", back_populates="appointments")


class Callback(Base):
    __tablename__ = "callbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id"), nullable=False, index=True
    )
    requested_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    lead = relationship("Lead", back_populates="callbacks")


class ConsentLog(Base):
    """Compliance audit trail: one row per disposition / consent event."""

    __tablename__ = "consent_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)

    lead = relationship("Lead", back_populates="consent_events")


class ProcessedWebhookEvent(Base):
    """Idempotency ledger so duplicate webhook deliveries are no-ops (Section 5)."""

    __tablename__ = "processed_webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", "event_type", name="uq_webhook_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
