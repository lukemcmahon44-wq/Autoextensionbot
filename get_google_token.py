#!/usr/bin/env python3
"""Mint a Google OAuth refresh token for Calendar access (run once, locally).

Prereqs: a Google Cloud project with the Calendar API enabled and an OAuth
*Desktop app* client. Put its id/secret in your environment or .env, then:

    python get_google_token.py

A browser opens for consent; the script prints GOOGLE_OAUTH_REFRESH_TOKEN to
paste into .env. Requires a machine with a browser (not a headless server).
"""
from __future__ import annotations

import os
import sys

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> int:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret):
        try:
            from voiceagent.config import get_settings

            s = get_settings()
            client_id = client_id or s.google_oauth_client_id
            client_secret = client_secret or s.google_oauth_client_secret
        except Exception:
            pass
    if not (client_id and client_secret):
        print("FAIL: set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (env or .env)")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(config, SCOPES)
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        if not creds.refresh_token:
            print("FAIL: no refresh token returned. Revoke the prior grant and retry.")
            return 1
        print("\nPASS — add this line to your .env:\n")
        print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
