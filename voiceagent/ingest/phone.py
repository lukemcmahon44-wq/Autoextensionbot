"""Phone normalization to E.164 + timezone inference.

Uses Google's libphonenumber (the ``phonenumbers`` package) for both validation
and timezone lookup, so we don't maintain a hand-rolled area-code table.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import phonenumbers
from phonenumbers import NumberParseException
from phonenumbers import timezone as pn_timezone

DEFAULT_REGION = "US"


@dataclass
class PhoneResult:
    e164: Optional[str]
    ok: bool
    reason: Optional[str] = None


def normalize_phone(raw, default_region: str = DEFAULT_REGION) -> PhoneResult:
    """Parse + validate a raw phone value and return it in E.164.

    Numbers without a country code are interpreted in ``default_region``.
    """
    if raw is None:
        return PhoneResult(None, False, "empty phone")
    s = str(raw).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return PhoneResult(None, False, "empty phone")

    # Spreadsheets often store phones as floats -> "4155551234.0"
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]

    try:
        num = phonenumbers.parse(s, default_region)
    except NumberParseException as exc:
        return PhoneResult(None, False, f"unparseable ({exc})")

    if not phonenumbers.is_possible_number(num):
        return PhoneResult(None, False, "not a possible number")
    if not phonenumbers.is_valid_number(num):
        return PhoneResult(None, False, "invalid number")

    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    return PhoneResult(e164, True)


def infer_timezone(e164: str, default: Optional[str] = None) -> Optional[str]:
    """Best-effort IANA timezone for an E.164 number; ``default`` if unknown."""
    try:
        num = phonenumbers.parse(e164, None)
    except NumberParseException:
        return default
    zones = pn_timezone.time_zones_for_number(num)
    if zones:
        first = zones[0]
        if first and first != "Etc/Unknown":
            return first
    return default
