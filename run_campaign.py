#!/usr/bin/env python3
"""Run an outbound campaign.

Optionally ingest a spreadsheet first, then place calls to new/retry leads that
are inside their local calling window, up to the concurrency cap.

    python run_campaign.py leads.xlsx              # ingest + place calls
    python run_campaign.py --dry-run               # show who'd be called now
    python run_campaign.py leads.xlsx --max 1      # place at most one call
"""
from __future__ import annotations

import argparse
import sys

from voiceagent.db.session import get_session, init_db
from voiceagent.orchestration.campaign import CampaignRunner


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run an outbound calling campaign.")
    parser.add_argument("spreadsheet", nargs="?", help="Optional .xlsx to ingest first")
    parser.add_argument("--max", type=int, default=None, help="Max calls to place this pass")
    parser.add_argument("--dry-run", action="store_true", help="List eligible leads; place no calls")
    args = parser.parse_args(argv)

    init_db()
    runner = CampaignRunner()

    if args.spreadsheet:
        runner.ingest_and_load(args.spreadsheet)

    if args.dry_run:
        with get_session() as session:
            eligible = runner.eligible_leads(session)
            print(f"{len(eligible)} lead(s) eligible right now (in calling window, not DNC):")
            for lead in eligible[:50]:
                print(f"  {lead.phone:16} {lead.name or '':20} status={lead.status.value} tz={lead.timezone}")
        return 0

    result = runner.run_once(max_calls=args.max)
    print(f"placed={result['placed']} errors={result['errors']} budget={result['budget']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
