"""Configuration loading and the fail-closed safety gate.

Two sources only:
  * config.yaml      -- strategy/risk knobs and the live-vs-paper default.
  * environment/.env -- SECRETS (API key id, private key path, webhook).

Secrets never come from config.yaml and are never logged.

Fail-closed rule: if the effective mode is LIVE we refuse to start unless a
usable API key id + a readable, parseable RSA private key are present. Paper
mode (config or FORCE_PAPER) is always allowed to start.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml

try:  # python-dotenv is optional at import time; .env is convenience only.
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore
        return False


def _as_bool(val: Optional[str]) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "on") if val is not None else False


class ConfigError(RuntimeError):
    """Raised when configuration is unsafe or incomplete. The bot must not start."""


@dataclass
class Secrets:
    api_key_id: Optional[str]
    private_key_path: Optional[str]
    webhook_url: Optional[str]


@dataclass
class Settings:
    raw: Dict[str, Any]
    paper: bool                 # effective mode after FORCE_PAPER is applied
    force_paper: bool           # whether the override was responsible
    api_base: str               # the base URL actually in use this run
    secrets: Secrets

    # convenience typed views -------------------------------------------------
    @property
    def strategy(self) -> Dict[str, Any]:
        return self.raw["strategy"]

    @property
    def risk(self) -> Dict[str, Any]:
        return self.raw["risk"]

    @property
    def kill_switch(self) -> Dict[str, Any]:
        return self.raw["kill_switch"]

    @property
    def loop(self) -> Dict[str, Any]:
        return self.raw["loop"]

    @property
    def mode(self) -> Dict[str, Any]:
        return self.raw["mode"]

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.raw.get(section, {}).get(key, default)


_REQUIRED_SECTIONS = ("mode", "strategy", "risk", "kill_switch", "loop")


def _validate_shape(raw: Dict[str, Any]) -> None:
    missing = [s for s in _REQUIRED_SECTIONS if s not in raw]
    if missing:
        raise ConfigError(f"config.yaml missing required sections: {missing}")
    s = raw["strategy"]
    if not (1 <= s["entry_min_cents"] <= s["entry_max_cents"] <= 99):
        raise ConfigError("strategy entry band must satisfy 1 <= min <= max <= 99")
    if s["stop_loss_cents"] >= s["entry_min_cents"]:
        # A stop at/above the entry band would fire instantly -- almost certainly a typo.
        raise ConfigError("stop_loss_cents should be below entry_min_cents")
    if raw["risk"]["max_position_usd"] <= 0 or raw["risk"]["max_total_exposure_usd"] <= 0:
        raise ConfigError("risk caps must be positive")


def _load_private_key_or_raise(path: Optional[str]):
    """Parse the RSA private key so we fail at startup, not at first order."""
    if not path:
        raise ConfigError("KALSHI_PRIVATE_KEY_PATH is required for live trading")
    if not os.path.isfile(path):
        raise ConfigError(f"private key file not found: {path}")
    from cryptography.hazmat.primitives import serialization

    with open(path, "rb") as fh:
        data = fh.read()
    try:
        return serialization.load_pem_private_key(data, password=None)
    except Exception as exc:  # noqa: BLE001 -- surface a clean message, never the key
        raise ConfigError(f"could not parse RSA private key at {path}: {exc}") from None


def load_settings(config_path: str = "config.yaml") -> Settings:
    load_dotenv()  # populate os.environ from .env if present; no-op otherwise

    if not os.path.isfile(config_path):
        raise ConfigError(f"config file not found: {config_path}")
    with open(config_path, "r") as fh:
        raw = yaml.safe_load(fh) or {}
    _validate_shape(raw)

    secrets = Secrets(
        api_key_id=os.environ.get("KALSHI_API_KEY_ID") or None,
        private_key_path=os.environ.get("KALSHI_PRIVATE_KEY_PATH") or None,
        webhook_url=os.environ.get("ALERT_WEBHOOK_URL") or None,
    )

    force_paper = _as_bool(os.environ.get("FORCE_PAPER"))
    config_paper = bool(raw["mode"].get("paper_trading", False))
    paper = config_paper or force_paper

    api_base = raw["mode"]["api_base_demo"] if paper else raw["mode"]["api_base_live"]

    # --- fail closed -------------------------------------------------------
    if not paper:
        if not secrets.api_key_id:
            raise ConfigError(
                "LIVE mode requires KALSHI_API_KEY_ID. Refusing to start. "
                "Set FORCE_PAPER=true to dry-run, or provide credentials."
            )
        # Parse the key now; raises ConfigError if missing/unreadable/invalid.
        _load_private_key_or_raise(secrets.private_key_path)

    return Settings(
        raw=raw,
        paper=paper,
        force_paper=force_paper,
        api_base=api_base,
        secrets=secrets,
    )
