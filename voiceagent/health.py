"""Lightweight, cheap validity checks for every external integration.

Each check returns a CheckResult. Where possible it makes a low-cost
authenticated call (list models, fetch account, get user) so "valid" really
means the credential works — not just that it's present.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from voiceagent.config import get_config, get_settings


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _missing(name: str, env: str) -> CheckResult:
    return CheckResult(name, False, f"missing {env}")


def check_anthropic() -> CheckResult:
    s = get_settings()
    if not s.anthropic_api_key:
        return _missing("anthropic", "ANTHROPIC_API_KEY")
    try:
        from anthropic import Anthropic

        models = Anthropic(api_key=s.anthropic_api_key).models.list(limit=1)
        sample = models.data[0].id if getattr(models, "data", None) else "ok"
        return CheckResult("anthropic", True, f"auth ok (e.g. {sample})")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("anthropic", False, f"{type(exc).__name__}: {exc}")


def check_elevenlabs() -> CheckResult:
    s = get_settings()
    if not s.elevenlabs_api_key:
        return _missing("elevenlabs", "ELEVENLABS_API_KEY")
    try:
        from elevenlabs.client import ElevenLabs

        user = ElevenLabs(api_key=s.elevenlabs_api_key).user.get()
        tier = getattr(getattr(user, "subscription", None), "tier", "ok")
        return CheckResult("elevenlabs", True, f"auth ok (tier={tier})")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("elevenlabs", False, f"{type(exc).__name__}: {exc}")


def check_twilio() -> CheckResult:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        return _missing("twilio", "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN")
    try:
        from voiceagent.telephony.twilio_client import TwilioClient

        info = TwilioClient(s).validate()
        return CheckResult("twilio", True, f"account {info.get('status')}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("twilio", False, f"{type(exc).__name__}: {exc}")


def check_retell() -> CheckResult:
    s = get_settings()
    if not s.retell_api_key:
        return _missing("retell", "RETELL_API_KEY")
    try:
        from retell import Retell

        agents = Retell(api_key=s.retell_api_key).agent.list()
        n = len(list(agents)) if agents is not None else 0
        return CheckResult("retell", True, f"auth ok ({n} agents)")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("retell", False, f"{type(exc).__name__}: {exc}")


def check_calendar() -> CheckResult:
    s = get_settings()
    if not (s.google_oauth_refresh_token or s.google_service_account_file):
        return _missing("calendar", "GOOGLE_OAUTH_REFRESH_TOKEN or GOOGLE_SERVICE_ACCOUNT_FILE")
    try:
        from voiceagent.gcal.google_calendar import GoogleCalendarClient

        info = GoogleCalendarClient(s, get_config().booking.calendar_id).validate()
        return CheckResult("calendar", True, f"calendar '{info.get('summary')}' ({info.get('timeZone')})")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("calendar", False, f"{type(exc).__name__}: {exc}")


ALL_CHECKS: list[Callable[[], CheckResult]] = [
    check_anthropic,
    check_elevenlabs,
    check_twilio,
    check_retell,
    check_calendar,
]


def run_all() -> list[CheckResult]:
    return [check() for check in ALL_CHECKS]


def print_results(results: list[CheckResult]) -> bool:
    all_ok = True
    for r in results:
        all_ok = all_ok and r.ok
        print(f"  [{'PASS' if r.ok else 'FAIL'}] {r.name:11} — {r.detail}")
    return all_ok
