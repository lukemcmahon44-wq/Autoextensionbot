"""Crash-safe state persistence so a restart resumes cleanly.

We persist the risk circuit state (daily baseline, error count, latch) and, in
paper mode, the simulated broker's balance/positions. State is written
atomically (temp file + rename) so a crash mid-write can't corrupt it.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict

log = logging.getLogger("kalshi.state")


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r") as fh:
            return json.load(fh) or {}
    except Exception as exc:  # noqa: BLE001 -- never let bad state crash startup
        log.warning("state: could not read %s (%s); starting fresh", path, exc)
        return {}


def save_state(path: str, state: Dict[str, Any]) -> None:
    try:
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)  # atomic on POSIX
    except Exception as exc:  # noqa: BLE001 -- best-effort; loop keeps running
        log.warning("state: could not write %s (%s)", path, exc)
