"""The safety layer. Every entry must pass ``check_entry`` and every loop must
call ``update_daily_pnl``. Nothing in the strategy may bypass this.

The guardrails enforced here:
  * kill switch file        -> instant global halt (optionally flatten)
  * daily loss limit        -> halt new entries for the day (optionally flatten)
  * max position / exposure / concurrent-positions caps
  * no-new-entries-before-close window (gap risk is worst right at the bell)
  * consecutive-error circuit breaker -> halt + alert

These are deliberately conservative and must not be loosened to "trade more".
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import timezone
from typing import Callable, Mapping, Optional

from .models import AccountState, EntryPlan, Market
from .timeutil import day_key, resolve_tz


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:  # lets callers write ``if decision:``
        return self.allowed


class RiskManager:
    def __init__(self, config: Mapping, *, now_fn: Callable[[], float] = time.time):
        risk = config["risk"]
        kill = config["kill_switch"]
        self._now = now_fn

        # caps
        self.max_position_usd = float(risk["max_position_usd"])
        self.max_total_exposure_usd = float(risk["max_total_exposure_usd"])
        self.max_concurrent_positions = int(risk["max_concurrent_positions"])
        self.daily_loss_limit_pct = float(risk["daily_loss_limit_pct"])
        self.flatten_on_daily_limit = bool(risk["flatten_on_daily_limit"])
        self.no_new_entries_before_close_sec = int(risk["no_new_entries_before_close_min"]) * 60
        self.max_consecutive_errors = int(risk["max_consecutive_errors"])

        # kill switch
        self.kill_file = kill["file"]
        self.flatten_on_kill = bool(kill.get("flatten_on_kill", True))

        # The daily loss limit resets on the exchange's trading day (US/Eastern
        # for Kalshi). Falls back to UTC if no timezone is configured.
        tz_name = config.get("strategy", {}).get("settlement_timezone")
        self._tz = resolve_tz(tz_name) or timezone.utc

        # mutable safety state (persistable across restarts via state.json)
        self.consecutive_errors = 0
        self.day_key: Optional[str] = None
        self.day_start_equity: Optional[float] = None
        self.daily_limit_hit = False
        self.last_equity: Optional[float] = None

    # ----- construction helpers ------------------------------------------
    @classmethod
    def from_settings(cls, settings, *, now_fn: Callable[[], float] = time.time) -> "RiskManager":
        return cls(settings.raw, now_fn=now_fn)

    def _today_key(self) -> str:
        return day_key(self._now(), self._tz)

    # ----- error circuit breaker -----------------------------------------
    def record_error(self) -> bool:
        """Count one transient failure. Returns True if the breaker is now tripped."""
        self.consecutive_errors += 1
        return self.circuit_broken()

    def record_success(self) -> None:
        self.consecutive_errors = 0

    def circuit_broken(self) -> bool:
        return self.consecutive_errors >= self.max_consecutive_errors

    # ----- kill switch ----------------------------------------------------
    def kill_switch_active(self) -> bool:
        return os.path.exists(self.kill_file)

    # ----- daily P&L ------------------------------------------------------
    def update_daily_pnl(self, current_equity: float) -> None:
        """Refresh the daily loss circuit against start-of-day equity.

        Resets the baseline on a new UTC day. Call this every loop with the
        reconciled equity (cash + marked positions).
        """
        today = self._today_key()
        if self.day_key != today or self.day_start_equity is None:
            self.day_key = today
            self.day_start_equity = current_equity
            self.daily_limit_hit = False
        self.last_equity = current_equity
        if self.day_start_equity and self.day_start_equity > 0:
            pnl_pct = (current_equity - self.day_start_equity) / self.day_start_equity * 100.0
            if pnl_pct <= -abs(self.daily_loss_limit_pct):
                self.daily_limit_hit = True

    def daily_pnl_pct(self) -> float:
        if not self.day_start_equity or self.last_equity is None:
            return 0.0
        return (self.last_equity - self.day_start_equity) / self.day_start_equity * 100.0

    # ----- global halt / flatten -----------------------------------------
    def halt_reason(self) -> Optional[str]:
        """Non-None => no new entries are allowed right now (global halt)."""
        if self.kill_switch_active():
            return f"kill switch present ({self.kill_file})"
        if self.daily_limit_hit:
            return f"daily loss limit hit ({self.daily_loss_limit_pct:.1f}%)"
        if self.circuit_broken():
            return f"error circuit breaker ({self.consecutive_errors} consecutive errors)"
        return None

    def global_halt(self) -> bool:
        """Kill switch or error breaker => stop *everything* (entries and
        discretionary exits); a human takes over. A pure daily-loss halt only
        blocks new entries, so it is intentionally excluded here.
        """
        return self.kill_switch_active() or self.circuit_broken()

    def should_flatten(self) -> bool:
        """Whether the current halt condition also demands flattening positions."""
        if self.kill_switch_active() and self.flatten_on_kill:
            return True
        if self.daily_limit_hit and self.flatten_on_daily_limit:
            return True
        return False

    # ----- sizing helper --------------------------------------------------
    def remaining_exposure_budget_usd(self, account: AccountState) -> float:
        return max(0.0, self.max_total_exposure_usd - account.total_exposure_usd())

    # ----- the gate every entry must pass --------------------------------
    def check_entry(self, plan: EntryPlan, account: AccountState, market: Market) -> RiskDecision:
        reason = self.halt_reason()
        if reason:
            return RiskDecision(False, f"halted: {reason}")

        if plan.count <= 0:
            return RiskDecision(False, "non-positive size")

        # Per-position cost cap (account for adding to an existing position).
        existing = account.positions.get(plan.ticker)
        prospective_position_cost = plan.cost_usd + (existing.cost_usd if existing else 0.0)
        if prospective_position_cost > self.max_position_usd + 1e-9:
            return RiskDecision(
                False,
                f"position cost ${prospective_position_cost:.2f} > cap ${self.max_position_usd:.2f}",
            )

        # Total exposure cap.
        if account.total_exposure_usd() + plan.cost_usd > self.max_total_exposure_usd + 1e-9:
            return RiskDecision(
                False,
                f"exposure ${account.total_exposure_usd() + plan.cost_usd:.2f} "
                f"> cap ${self.max_total_exposure_usd:.2f}",
            )

        # Concurrency cap -- only a *new* ticker consumes a slot.
        if existing is None and len(account.positions) >= self.max_concurrent_positions:
            return RiskDecision(
                False,
                f"already at max_concurrent_positions ({self.max_concurrent_positions})",
            )

        # No entries in the final minutes before close.
        seconds_to_close = market.close_ts - self._now()
        if seconds_to_close <= self.no_new_entries_before_close_sec:
            return RiskDecision(
                False,
                f"within {self.no_new_entries_before_close_sec // 60} min of close",
            )

        # Must be able to afford it (exchange is source of truth on cash).
        if plan.cost_usd > account.balance_usd + 1e-9:
            return RiskDecision(
                False,
                f"insufficient balance ${account.balance_usd:.2f} for ${plan.cost_usd:.2f}",
            )

        return RiskDecision(True, "ok")

    # ----- persistence ----------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "consecutive_errors": self.consecutive_errors,
            "day_key": self.day_key,
            "day_start_equity": self.day_start_equity,
            "daily_limit_hit": self.daily_limit_hit,
            "last_equity": self.last_equity,
        }

    def restore(self, snap: Mapping) -> None:
        if not snap:
            return
        self.consecutive_errors = int(snap.get("consecutive_errors", 0))
        self.day_key = snap.get("day_key")
        self.day_start_equity = snap.get("day_start_equity")
        self.daily_limit_hit = bool(snap.get("daily_limit_hit", False))
        self.last_equity = snap.get("last_equity")
