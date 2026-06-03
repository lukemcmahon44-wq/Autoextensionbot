#!/usr/bin/env python3
"""One-command health check: are all API keys present and valid?

Exit code 0 only if every integration passes. Run this before anything else.
"""
from __future__ import annotations

import sys

from voiceagent.health import print_results, run_all


def main() -> int:
    print("Voice Sales Agent — health check\n")
    ok = print_results(run_all())
    print(f"\n{'PASS' if ok else 'FAIL'}: all integrations" if ok else "\nFAIL: one or more integrations need attention")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
