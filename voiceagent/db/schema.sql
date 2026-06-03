-- ===========================================================================
-- voiceagent — reference schema (SQLite dialect).
--
-- This file is a human-readable REFERENCE. The authoritative schema lives in
-- the SQLAlchemy models (voiceagent/db/models.py) and Alembic migrations
-- (/alembic). Postgres mapping: INTEGER PRIMARY KEY -> GENERATED IDENTITY,
-- TIMESTAMP/BOOLEAN are native, CHECK constraints carry over unchanged.
-- ===========================================================================

CREATE TABLE leads (
    id             INTEGER PRIMARY KEY,
    name           TEXT,
    phone          TEXT NOT NULL UNIQUE,            -- E.164; de-dupe key
    business_name  TEXT,
    email          TEXT,
    notes          TEXT,
    timezone       TEXT,                            -- IANA, e.g. America/New_York
    source_file    TEXT,
    status         TEXT NOT NULL DEFAULT 'new'
                   CHECK (status IN ('new','calling','retry','booked',
                          'not_interested','callback','no_answer',
                          'voicemail','bad_number','dnc')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_called_at TIMESTAMP,
    do_not_call    BOOLEAN NOT NULL DEFAULT 0,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_leads_status ON leads (status);

CREATE TABLE calls (
    id                INTEGER PRIMARY KEY,
    lead_id           INTEGER NOT NULL REFERENCES leads (id),
    retell_call_id    TEXT UNIQUE,                  -- idempotency anchor
    started_at        TIMESTAMP,
    ended_at          TIMESTAMP,
    duration_s        INTEGER,
    outcome           TEXT,
    transcript        TEXT,
    recording_url     TEXT,
    cost              REAL,                          -- total
    cost_breakdown    TEXT,                          -- JSON: retell/twilio/11labs
    disposition_notes TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_calls_lead_id ON calls (lead_id);

CREATE TABLE appointments (
    id                INTEGER PRIMARY KEY,
    lead_id           INTEGER NOT NULL REFERENCES leads (id),
    calendar_event_id TEXT,
    scheduled_for     TIMESTAMP NOT NULL,
    timezone          TEXT NOT NULL,
    confirmed         BOOLEAN NOT NULL DEFAULT 0,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_appointments_lead_id ON appointments (lead_id);

CREATE TABLE callbacks (
    id            INTEGER PRIMARY KEY,
    lead_id       INTEGER NOT NULL REFERENCES leads (id),
    requested_for TIMESTAMP NOT NULL,
    notes         TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE consent_log (
    id         INTEGER PRIMARY KEY,
    lead_id    INTEGER NOT NULL REFERENCES leads (id),
    timestamp  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT NOT NULL,   -- call_initiated | ai_disclosure_given |
                                -- opt_out_requested | dnc_set | disposition
    detail     TEXT
);
CREATE INDEX ix_consent_log_lead_id ON consent_log (lead_id);

CREATE TABLE processed_webhook_events (
    id          INTEGER PRIMARY KEY,
    provider    TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, event_id, event_type)
);
