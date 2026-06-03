#!/usr/bin/env python3
"""Standalone test: full Excel ingest on a generated messy sample sheet.

No external services, no DB writes. Prints a clear PASS/FAIL with details.
Asserts robust properties (E.164 output, de-dupe, rejection routing, tz
inference) rather than brittle exact counts.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from voiceagent.ingest.excel import ingest_file, write_rejected
from voiceagent.ingest.sample import make_sample

E164 = re.compile(r"^\+\d{6,15}$")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    sample = make_sample(tmp / "sample_leads.xlsx")
    print(f"Generated messy sample: {sample}\n")

    result = ingest_file(sample, default_timezone="America/New_York")
    print(result.summary())

    print("\nLoaded leads:")
    for lead in result.leads:
        print(f"  {lead.phone:16} tz={lead.timezone!s:18} {lead.name} / {lead.business_name}")
    print("Rejected rows:")
    for r in result.rejected:
        print(f"  row {r.row_number}: {r.reason}  (name={r.data.get('name')})")

    checks: list[tuple[str, bool]] = []
    checks.append(("all loaded phones are E.164", all(E164.match(l.phone) for l in result.leads)))
    checks.append((">=2 valid leads loaded", len(result.leads) >= 2))
    checks.append((">=1 duplicate merged", result.duplicates_merged >= 1))
    checks.append((">=3 rows rejected", len(result.rejected) >= 3))
    checks.append(("every rejection has a reason", all(r.reason for r in result.rejected)))
    checks.append(("tz inferred for >=1 lead w/o tz column", any(l.timezone for l in result.leads)))

    rej_path = write_rejected(result.rejected, tmp / "rejected_leads.xlsx")
    checks.append(("rejected_leads.xlsx written", bool(rej_path and rej_path.exists())))
    if rej_path:
        import pandas as pd

        df = pd.read_excel(rej_path, engine="openpyxl")
        checks.append(("rejected file has rejection_reason column", "rejection_reason" in df.columns))

    print()
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    print(f"\n{'PASS' if ok else 'FAIL'}: Excel ingest")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
