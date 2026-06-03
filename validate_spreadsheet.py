#!/usr/bin/env python3
"""Dry-run spreadsheet validation.

Runs the FULL ingest pipeline (header aliasing, E.164 normalization, timezone
inference, in-file de-dupe, rejection) and writes ``rejected_leads.xlsx``
WITHOUT touching the database — so you can clean your data before a campaign.

Usage:
    python validate_spreadsheet.py <leads.xlsx> [--out rejected_leads.xlsx]
"""
from __future__ import annotations

import argparse
import sys

from voiceagent.config import get_config
from voiceagent.ingest.excel import ingest_file, write_rejected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a leads spreadsheet (dry run).")
    parser.add_argument("spreadsheet", help="Path to the .xlsx file")
    parser.add_argument("--out", default="rejected_leads.xlsx", help="Rejected-rows output path")
    args = parser.parse_args(argv)

    cfg = get_config()
    try:
        result = ingest_file(args.spreadsheet, default_timezone=cfg.calling.default_timezone)
    except Exception as exc:  # noqa: BLE001 - surface the real error to the user
        print(f"FAIL: could not read {args.spreadsheet}: {exc}", file=sys.stderr)
        return 2

    print(result.summary())
    path = write_rejected(result.rejected, args.out)
    if path:
        print(f"\nWrote {len(result.rejected)} rejected row(s) to {path}")
    else:
        print("\nNo rejected rows.")
    print("\n(Dry run — nothing was written to the database.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
