"""The hands-off trading loop.

Crash-resistant: the body of every cycle is wrapped; on failure we alert,
``record_error()`` into the circuit breaker, persist state, and keep going.
State is persisted to ``state.json`` so a restart resumes cleanly.

Run a safe dry-run with:   FORCE_PAPER=true python -m src.main
The shipped config default is LIVE -- the owner runs it with their own keys.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from typing import Dict, Optional

from . import config as config_mod
from .executor import Executor, LiveBroker, PaperBroker
from .kalshi_client import KalshiClient
from .marketdata import LiveMarketData, SimMarketData, set_sim_clock
from .models import AccountState, Market, Position, cents_to_usd
from .notifier import Notifier
from .risk import RiskManager
from .scanner import Scanner
from .state import load_state, save_state
from .strategy import plan_entry, should_exit

log = logging.getLogger("kalshi.main")


class Clock:
    """Wall clock, optionally accelerated for offline paper testing.

    Acceleration is *only* ever applied in paper mode -- live trading always runs
    at real time. ``set scale`` so a short paper run can simulate a whole session.
    """

    def __init__(self, scale: float = 1.0):
        self.start = time.time()
        self.scale = max(1.0, scale)

    def now(self) -> float:
        return self.start + (time.time() - self.start) * self.scale


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


class App:
    def __init__(self, settings, clock: Clock, *, persist: bool = True):
        self.settings = settings
        self.clock = clock
        self.persist = persist
        self.cfg = settings.raw
        self.strategy_cfg = settings.strategy
        self.notifier = Notifier(settings.secrets.webhook_url, paper=settings.paper)

        set_sim_clock(clock.now)  # so SimMarketData marks to the loop's clock

        data_source, broker = self._build_market_layer()
        self.data_source = data_source
        self.executor = Executor(
            broker,
            data_source,
            self.strategy_cfg,
            day_fn=lambda: datetime.fromtimestamp(self.clock.now()).strftime("%Y%m%d"),
        )
        self.risk = RiskManager.from_settings(settings, now_fn=clock.now)
        self.scanner = Scanner(self.strategy_cfg, now_fn=clock.now)
        self.state_path = settings.loop["state_file"]
        self.entry_ttl_cycles = int(settings.loop.get("entry_order_ttl_cycles", 3))
        self.last_summary_day: Optional[str] = None
        self.last_bid: Dict[str, int] = {}   # last seen YES bid per held ticker
        self._restore()

    # ----- wiring ---------------------------------------------------------
    def _make_client(self) -> Optional[KalshiClient]:
        s = self.settings
        if not (s.secrets.api_key_id and s.secrets.private_key_path):
            return None
        order_cfg = s.raw.get("order", {}) or {}
        return KalshiClient(
            s.api_base,
            s.secrets.api_key_id,
            s.secrets.private_key_path,
            time_in_force=order_cfg.get("time_in_force"),
            reduce_only_sells=bool(order_cfg.get("reduce_only", False)),
        )

    def _build_market_layer(self):
        s = self.settings
        if not s.paper:
            client = self._make_client()  # creds are guaranteed present (fail-closed)
            log.info("LIVE mode: real-money trading against %s", s.api_base)
            return LiveMarketData(client), LiveBroker(client)

        # ---- paper mode ----
        source_pref = s.mode.get("paper_data_source", "auto")
        start_balance = _env_float("PAPER_START_BALANCE", float(s.mode.get("paper_start_balance", 1000.0)))
        broker = PaperBroker(start_balance, fee_rate=float(s.mode.get("paper_fee_rate", 0.0)))

        if source_pref in ("auto", "live"):
            client = self._make_client()
            if client is not None:
                try:
                    client.get_exchange_status()  # reachability + auth probe
                    log.info("PAPER mode: live demo data from %s (simulated fills)", s.api_base)
                    return LiveMarketData(client), broker
                except Exception as exc:  # noqa: BLE001
                    log.warning("PAPER: demo host unreachable (%s)", exc)
                    if source_pref == "live":
                        log.warning("paper_data_source=live but unreachable; falling back to sim")
            elif source_pref == "live":
                log.warning("paper_data_source=live but no credentials; falling back to sim")

        log.info("PAPER mode: OFFLINE simulator (no network, simulated fills)")
        m = s.mode
        sim = SimMarketData(
            self.clock.now(),
            self.strategy_cfg["max_hours_to_close"],
            seed=int(m.get("sim_seed", 7)),
            n=int(m.get("sim_markets", 6)),
            win_prob=m.get("sim_win_prob", 0.6),
            gap_prob=float(m.get("sim_gap_prob", 0.0)),
        )
        return sim, broker

    # ----- persistence ----------------------------------------------------
    def _restore(self):
        if not self.persist:
            return
        st = load_state(self.state_path)
        self.risk.restore(st.get("risk", {}))
        self.executor._seq = int(st.get("executor_seq", 0))
        self.last_summary_day = st.get("last_summary_day")
        if self.settings.paper and isinstance(self.executor.broker, PaperBroker):
            self.executor.broker.restore(st.get("paper_broker", {}))
        if st:
            log.info("restored state from %s", self.state_path)

    def _persist(self):
        if not self.persist:
            return
        st = {
            "risk": self.risk.snapshot(),
            "executor_seq": self.executor._seq,
            "last_summary_day": self.last_summary_day,
        }
        if self.settings.paper and isinstance(self.executor.broker, PaperBroker):
            st["paper_broker"] = self.executor.broker.snapshot()
        save_state(self.state_path, st)

    # ----- one cycle ------------------------------------------------------
    def _market_for_ticker(self, ticker: str) -> Market:
        bid, ask, bd, ad = self.data_source.orderbook_top(ticker)
        return Market(ticker, "open", 0, bid, ask, ad, bd)

    def run_cycle(self):
        now = self.clock.now()

        # 1) reconcile from the source of truth (exchange / paper broker).
        account = self.executor.reconcile(now)
        # Resolve any resting orders from prior cycles against reconciled state.
        self.executor.reap_inflight_entries(account, self.entry_ttl_cycles)
        marks: Dict[str, Optional[int]] = {}
        for t in account.positions:
            bid = self.data_source.orderbook_top(t)[0]
            if bid is not None:
                self.last_bid[t] = bid
            # If a held position has no live bid (illiquid / gapped), mark off the
            # LAST bid we saw -- so a gap toward 0 still shows in equity and can trip
            # the daily-loss limit, instead of hiding at cost basis until settlement.
            marks[t] = bid if bid is not None else self.last_bid.get(t)
        self.last_bid = {t: v for t, v in self.last_bid.items() if t in account.positions}
        equity = account.equity_usd(marks)
        self.risk.update_daily_pnl(equity)
        log.info(
            "cycle: equity=$%.2f day_pnl=%+.2f%% cash=$%.2f positions=%d",
            equity, self.risk.daily_pnl_pct(), account.balance_usd, len(account.positions),
        )

        self._maybe_daily_summary(equity, len(account.positions))

        # 2) halt / flatten handling.
        halt = self.risk.halt_reason()
        if self.risk.should_flatten():
            market_by_ticker = {t: self._market_for_ticker(t) for t in account.positions}
            self.executor.flatten_all(account, market_by_ticker)
            self.notifier.halt(halt or "flatten requested", flattened=True)
            self.risk.record_success()   # reconcile succeeded -> a healthy cycle
            self._persist()
            return
        if self.risk.global_halt():
            # Kill switch: freeze everything this cycle (a human takes over).
            log.warning("global halt active: %s", halt)
            self.notifier.halt(halt or "global halt")
            self.risk.record_success()   # reconcile succeeded -> clear any error breaker
            self._persist()
            return

        # 3) manage exits (stops / take-profit) on existing positions.
        for ticker, pos in list(account.positions.items()):
            market = self._market_for_ticker(ticker)
            decision = should_exit(pos, market, self.strategy_cfg)
            if decision is None:
                continue
            new_exit = not self.executor.has_inflight_exit(ticker)
            res = self.executor.place_exit(decision, pos, market)
            if res.ok and res.filled_count > 0:
                self.notifier.trade("sell", ticker, res.filled_count,
                                    res.avg_fill_cents or decision.limit_price_cents, decision.reason)
            elif res.ok and new_exit:
                # Live: the sell is working and will fill asynchronously.
                self.notifier.order("sell", ticker, decision.count, decision.limit_price_cents)
            account = self.executor.broker.get_account()

        # 4) entries (only if new entries are not halted).
        if halt:
            log.info("entries halted: %s", halt)
        else:
            self._scan_and_enter(account)

        self.risk.record_success()
        self._persist()

    def _scan_and_enter(self, account):
        # Work on a copy we annotate with this cycle's just-placed orders. Live
        # fills are async, so without this several entries placed in one cycle
        # would each be sized against the same budget and could collectively
        # breach the exposure/position/concurrency caps once they fill.
        working = AccountState(account.balance_usd, dict(account.positions))
        # Count still-resting prior-cycle entries toward the caps too (the exchange
        # already reserves their funds, so we don't touch the reconciled balance).
        for tk, pos in self.executor.inflight_entry_positions().items():
            cur = working.positions.get(tk)
            if cur is None:
                working.positions[tk] = pos
            else:
                # Conservatively ADD the resting order on top of the reconciled
                # position. A rare partial-fill overlap double-counts the filled
                # part -- which only tightens the caps (safe), never loosens them.
                total = cur.count + pos.count
                avg = round((cur.cost_usd + pos.cost_usd) / cents_to_usd(1) / total)
                working.positions[tk] = Position(tk, total, max(1, min(99, avg)))
        for m in self.scanner.scan(self.data_source):
            if self.risk.orders_exhausted():
                log.info("daily order cap reached; no more entries this cycle")
                break
            # Don't stack a second order on a market whose entry is still resting.
            if self.executor.has_inflight_entry(m.ticker):
                log.info("skip %s: entry already resting", m.ticker)
                continue
            remaining = self.risk.remaining_exposure_budget_usd(working)
            plan = plan_entry(
                m, working, self.strategy_cfg,
                max_position_usd=self.risk.max_position_usd,
                remaining_exposure_usd=remaining,
            )
            if plan is None:
                continue
            decision = self.risk.check_entry(plan, working, m)
            if not decision:
                log.info("entry blocked %s: %s", m.ticker, decision.reason)
                continue
            res = self.executor.place_entry(plan, m)
            if not res.ok:
                log.warning("entry order failed %s: %s", m.ticker, res.error)
                continue
            self.risk.record_order()   # count toward the daily order-cap throttle
            # Reflect the committed order in the working account (cost basis at the
            # limit) so the caps hold for the rest of this cycle, fill or not.
            self._commit_to_working(working, plan)
            if res.filled_count > 0:
                self.notifier.trade("buy", m.ticker, res.filled_count,
                                    res.avg_fill_cents or plan.limit_price_cents)
            else:
                # Live limit orders fill asynchronously -- report the working order
                # now; the fill shows up in the next reconcile + daily summary.
                self.notifier.order("buy", m.ticker, plan.count, plan.limit_price_cents)

    @staticmethod
    def _commit_to_working(working: AccountState, plan) -> None:
        existing = working.positions.get(plan.ticker)
        if existing:
            total = existing.count + plan.count
            avg = round((existing.cost_usd + plan.cost_usd) / cents_to_usd(1) / total)
            working.positions[plan.ticker] = Position(plan.ticker, total, max(1, min(99, avg)))
        else:
            working.positions[plan.ticker] = Position(plan.ticker, plan.count, plan.limit_price_cents)
        working.balance_usd -= plan.cost_usd

    def _maybe_daily_summary(self, equity: float, positions: int):
        day = self.risk.day_key
        if self.last_summary_day is None:
            self.last_summary_day = day
            return
        if day != self.last_summary_day:
            self.notifier.daily_summary(equity, self.risk.daily_pnl_pct(), positions)
            self.last_summary_day = day


_STOP = {"flag": False}


def _install_signal_handlers():
    def _handler(signum, _frame):
        log.info("received signal %s; stopping after this cycle", signum)
        _STOP["flag"] = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except Exception:  # pragma: no cover -- e.g. non-main thread
            pass


def run(settings, *, max_cycles: int = 0, poll_interval: Optional[float] = None) -> None:
    # Acceleration only in paper; live always runs at real time.
    scale = _env_float("SIM_TIME_SCALE", 1.0) if settings.paper else 1.0
    app = App(settings, Clock(scale=scale))
    interval = poll_interval if poll_interval is not None else float(settings.loop["poll_interval_sec"])
    _install_signal_handlers()

    log.info("starting loop: paper=%s interval=%.1fs max_cycles=%s scale=%.1f",
             settings.paper, interval, max_cycles or "inf", scale)

    # One-time startup alert so a hands-off deploy announces it came up + its limits.
    r, st = settings.risk, settings.strategy
    app.notifier.startup(
        f"band={st['entry_min_cents']}-{st['entry_max_cents']}c stop={st['stop_loss_cents']}c "
        f"pos<=${r['max_position_usd']} total<=${r['max_total_exposure_usd']} "
        f"concurrent<={r['max_concurrent_positions']} daily_stop={r['daily_loss_limit_pct']}%"
    )
    cycle = 0
    while not _STOP["flag"]:
        cycle += 1
        try:
            app.run_cycle()
        except Exception as exc:  # noqa: BLE001 -- the loop must survive anything
            log.exception("cycle %d failed", cycle)
            was_broken = app.risk.circuit_broken()
            tripped = app.risk.record_error()
            # Alert each failure until the breaker trips; then a single HALT; then
            # stay quiet until a healthy cycle clears it (no alert storm).
            if not was_broken and not tripped:
                app.notifier.error(f"cycle {cycle} failed: {exc}")
            if tripped:
                app.notifier.halt(app.risk.halt_reason() or "error circuit breaker")
            app._persist()
        if max_cycles and cycle >= max_cycles:
            log.info("reached max_cycles=%d; exiting", max_cycles)
            break
        if _STOP["flag"]:
            break
        time.sleep(interval)
    log.info("loop stopped after %d cycles", cycle)


def main() -> None:
    try:
        settings = config_mod.load_settings(os.environ.get("CONFIG_PATH", "config.yaml"))
    except config_mod.ConfigError as exc:
        # Fail closed with a clean message instead of a stack trace.
        print(f"REFUSING TO START: {exc}", flush=True)
        raise SystemExit(2)
    logging.basicConfig(
        level=getattr(logging, settings.raw.get("logging", {}).get("level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    max_cycles = int(_env_float("MAX_CYCLES", 0))
    poll = _env_float("POLL_INTERVAL_SEC", float(settings.loop["poll_interval_sec"]))
    run(settings, max_cycles=max_cycles, poll_interval=poll)


if __name__ == "__main__":
    main()
