"""Forgiving Excel ingest.

Accepts ``.xlsx`` with columns in ANY order, case-insensitive headers, and
common aliases. Normalizes phones to E.164, infers timezone when missing,
de-dupes on phone, and routes invalid rows to ``rejected_leads.xlsx`` with a
reason — never silently dropped.

``ingest_file`` is pure (no DB). ``persist`` writes a result to the DB and
de-dupes against existing leads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from voiceagent.ingest.phone import infer_timezone, normalize_phone

# canonical field -> accepted header aliases (compared after normalization)
HEADER_ALIASES: dict[str, str] = {
    "name": "name",
    "full_name": "name",
    "fullname": "name",
    "contact": "name",
    "contact_name": "name",
    "phone": "phone",
    "phone_number": "phone",
    "phonenumber": "phone",
    "cell": "phone",
    "cell_phone": "phone",
    "mobile": "phone",
    "telephone": "phone",
    "tel": "phone",
    "company": "business_name",
    "business": "business_name",
    "business_name": "business_name",
    "businessname": "business_name",
    "organization": "business_name",
    "org": "business_name",
    "email": "email",
    "email_address": "email",
    "e_mail": "email",
    "notes": "notes",
    "note": "notes",
    "comments": "notes",
    "comment": "notes",
    "timezone": "timezone",
    "time_zone": "timezone",
    "tz": "timezone",
}

CANONICAL_FIELDS = ["name", "phone", "business_name", "email", "notes", "timezone"]


@dataclass
class LeadRecord:
    phone: str
    name: Optional[str] = None
    business_name: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    timezone: Optional[str] = None
    source_file: Optional[str] = None


@dataclass
class RejectedRow:
    row_number: int
    data: dict[str, Any]
    reason: str


@dataclass
class IngestResult:
    source_file: str
    leads: list[LeadRecord] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    duplicates_merged: int = 0

    def summary(self) -> str:
        lines = [
            f"Ingest summary for {self.source_file}:",
            f"  {len(self.leads)} loaded",
            f"  {len(self.rejected)} rejected",
            f"  {self.duplicates_merged} duplicates merged",
        ]
        if self.rejected:
            lines.append("  rejection reasons:")
            counts: dict[str, int] = {}
            for r in self.rejected:
                counts[r.reason] = counts.get(r.reason, 0) + 1
            for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                lines.append(f"    - {reason}: {n}")
        return "\n".join(lines)


def _normalize_header(raw: str) -> str:
    h = str(raw).strip().lower()
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    return h


def map_headers(columns) -> dict[str, str]:
    """Map raw spreadsheet columns -> canonical field names (unknowns dropped)."""
    mapping: dict[str, str] = {}
    for col in columns:
        canon = HEADER_ALIASES.get(_normalize_header(col))
        if canon:
            mapping[col] = canon
    return mapping


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    return s


def ingest_file(path: str | Path, default_timezone: Optional[str] = None) -> IngestResult:
    """Read + validate a spreadsheet into an IngestResult. Does NOT touch the DB."""
    path = Path(path)
    result = IngestResult(source_file=path.name)
    df = pd.read_excel(path, engine="openpyxl", dtype=object)
    header_map = map_headers(df.columns)

    if "phone" not in set(header_map.values()):
        raise ValueError(
            f"No phone column found. Headers seen: {list(df.columns)}. "
            f"Accepted aliases include: phone, phone_number, cell, mobile."
        )

    seen_phones: set[str] = set()
    for idx, row in df.iterrows():
        row_number = int(idx) + 2  # +2: header row + 1-based
        canonical: dict[str, Any] = {}
        for raw_col, canon in header_map.items():
            canonical.setdefault(canon, _clean(row.get(raw_col)))

        phone_res = normalize_phone(canonical.get("phone"))
        if not phone_res.ok:
            result.rejected.append(
                RejectedRow(row_number, canonical, f"phone: {phone_res.reason}")
            )
            continue

        e164 = phone_res.e164
        if e164 in seen_phones:
            result.duplicates_merged += 1
            continue
        seen_phones.add(e164)

        tz = canonical.get("timezone") or infer_timezone(e164, default_timezone)
        result.leads.append(
            LeadRecord(
                phone=e164,
                name=canonical.get("name"),
                business_name=canonical.get("business_name"),
                email=canonical.get("email"),
                notes=canonical.get("notes"),
                timezone=tz,
                source_file=path.name,
            )
        )
    return result


def write_rejected(rejected: list[RejectedRow], out_path: str | Path = "rejected_leads.xlsx") -> Optional[Path]:
    """Write rejected rows (with a reason column) to xlsx. Returns path or None."""
    if not rejected:
        return None
    out_path = Path(out_path)
    rows = []
    for r in rejected:
        record = dict(r.data)
        record["row_number"] = r.row_number
        record["rejection_reason"] = r.reason
        rows.append(record)
    df = pd.DataFrame(rows)
    # put bookkeeping columns last
    cols = [c for c in df.columns if c not in ("row_number", "rejection_reason")]
    df = df[["row_number", *cols, "rejection_reason"]]
    df.to_excel(out_path, index=False, engine="openpyxl")
    return out_path


def persist(result: IngestResult, session) -> dict[str, int]:
    """Insert loaded leads into the DB, skipping phones that already exist.

    De-dupe is enforced both in-file (during ``ingest_file``) and against the DB
    here, so re-running ingest is safe (idempotent on phone).
    """
    from voiceagent.db.models import Lead  # local import to keep ingest DB-agnostic

    inserted = 0
    skipped_existing = 0
    existing = {p for (p,) in session.query(Lead.phone).all()}
    for rec in result.leads:
        if rec.phone in existing:
            skipped_existing += 1
            continue
        session.add(
            Lead(
                name=rec.name,
                phone=rec.phone,
                business_name=rec.business_name,
                email=rec.email,
                notes=rec.notes,
                timezone=rec.timezone,
                source_file=rec.source_file,
            )
        )
        existing.add(rec.phone)
        inserted += 1
    session.flush()
    return {"inserted": inserted, "skipped_existing": skipped_existing}
