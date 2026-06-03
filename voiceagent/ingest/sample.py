"""Generate a deliberately messy sample leads spreadsheet for testing ingest.

Exercises: columns in non-canonical order, mixed-case aliased headers, a phone
stored as a float, an exact duplicate, an explicit timezone, and three
unambiguously-bad rows (unparseable / empty / too-short).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# Columns intentionally out of order and using aliases / odd casing.
_ROWS = [
    {"Company": "Acme Co",  "Comments": "warm lead",      "Cell": "(415) 555-2671",  "Full Name": "Jane Doe",   "E-Mail": "jane@acme.com", "TZ": ""},
    {"Company": "Dup Inc",  "Comments": "same # as Jane",  "Cell": "+1 415 555 2671", "Full Name": "Bob Dupe",   "E-Mail": "bob@dup.com",   "TZ": ""},
    {"Company": "NYC LLC",  "Comments": "east coast",      "Cell": "212-555-0199",    "Full Name": "Carlos Ruiz","E-Mail": "c@nyc.com",     "TZ": ""},
    {"Company": "UK Ltd",   "Comments": "intl + explicit", "Cell": "+447911123456",   "Full Name": "Mary Major", "E-Mail": "m@uk.co",       "TZ": "Europe/London"},
    {"Company": "Oops",     "Comments": "not a phone",     "Cell": "not-a-phone",     "Full Name": "Bad Row",    "E-Mail": "bad@x.com",     "TZ": ""},
    {"Company": "NoPhone",  "Comments": "missing phone",   "Cell": "",                "Full Name": "Empty Phone","E-Mail": "n@x.com",       "TZ": ""},
    {"Company": "ShortCo",  "Comments": "too short",       "Cell": "12345",           "Full Name": "Short Num",  "E-Mail": "s@x.com",       "TZ": ""},
    {"Company": "FloatCo",  "Comments": "stored as float", "Cell": 16502530000.0,     "Full Name": "Dana Float", "E-Mail": "d@x.com",       "TZ": ""},
]


def make_sample(path: str | Path = "sample_leads.xlsx") -> Path:
    path = Path(path)
    pd.DataFrame(_ROWS).to_excel(path, index=False, engine="openpyxl")
    return path


if __name__ == "__main__":
    print("Wrote", make_sample())
