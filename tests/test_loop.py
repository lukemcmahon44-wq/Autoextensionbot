"""Integration tests for the wired loop in offline paper/sim mode."""
import datetime
import os

import pytest
import yaml

from src import config as config_mod
from src.main import App
from src.models import AccountState, OrderResult, Position


class StaticDS:
    """A data source with a fixed top-of-book and no scan candidates."""

    def __init__(self, bid, ask=None):
        self.bid = bid
        self.ask = ask if ask is not None else (bid + 1 if bid else None)

    def list_candidate_markets(self, now, max_close_ts):
        return []

    def orderbook_top(self, ticker):
        return (self.bid, self.ask, 500 if self.bid else 0, 500 if self.ask else 0)

    def settlement_value_cents(self, ticker, now):
        return None


class FixedClock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t


class RestingBroker:
    """A broker whose orders never fill immediately (like live limit orders)."""

    def __init__(self, balance=1000.0):
        self.account = AccountState(balance, {})
        self.created = []

    def get_account(self):
        return self.account

    def create_order(self, order, market):
        self.created.append(order)
        return OrderResult(ok=True, order_id="o-" + order.client_order_id, filled_count=0)

    def cancel_order(self, order_id):
        pass

    def settle_closed(self, ds, now):
        pass


class RecordingNotifier:
    def __init__(self):
        self.events = []

    def startup(self, *a, **k):
        pass

    def trade(self, *a, **k):
        self.events.append(("trade",) + a)

    def order(self, *a, **k):
        self.events.append(("order",) + a)

    def halt(self, *a, **k):
        self.events.append(("halt",) + a)

    def kill(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def daily_summary(self, *a, **k):
        pass


def _boot(config, tmp_path, monkeypatch):
    config["mode"]["paper_trading"] = True
    config["mode"]["paper_data_source"] = "sim"   # force offline simulator
    config["loop"]["state_file"] = "state.json"
    monkeypatch.chdir(tmp_path)                    # isolate state.json / KILL
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("FORCE_PAPER", "true")
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    settings = config_mod.load_settings("config.yaml")
    # Fix the clock at 9am local so simulated markets reliably close the SAME day,
    # regardless of the wall-clock time the test runs at.
    t = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    return App(settings, FixedClock(t))


def test_loop_runs_and_persists(config, tmp_path, monkeypatch):
    app = _boot(config, tmp_path, monkeypatch)
    for _ in range(8):
        app.run_cycle()                            # must not raise
    assert os.path.exists("state.json")
    assert app.executor.broker.balance_usd >= 0
    # The seeded simulator always presents at least one in-band market early on.
    # By now we should have transacted at least once (an entry or a settlement).
    assert app.executor._seq >= 1


def test_loop_kill_switch_flattens(config, tmp_path, monkeypatch):
    app = _boot(config, tmp_path, monkeypatch)  # no settlement, positions persist
    app.run_cycle()
    assert app.executor.broker.positions, "expected at least one open position after a cycle"
    # Drop the kill switch and run one more cycle.
    (tmp_path / "KILL").write_text("halt")
    app.run_cycle()
    assert app.executor.broker.positions == {}, "kill switch must flatten within one cycle"


def test_working_order_alerts_when_fill_is_async(config, tmp_path, monkeypatch):
    # In live, a limit order's create response reports 0 fills (it fills later).
    # The loop must still alert -- as a "working" order -- not stay silent.
    config["mode"]["paper_trading"] = True
    config["mode"]["paper_data_source"] = "sim"
    config["loop"]["state_file"] = "state.json"
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("FORCE_PAPER", "true")
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    settings = config_mod.load_settings("config.yaml")

    # Fix the clock at 9am local so the simulated markets close the same day.
    t = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    app = App(settings, FixedClock(t), persist=False)
    app.executor.broker = RestingBroker(1000.0)   # orders rest instead of filling
    rec = RecordingNotifier()
    app.notifier = rec

    app.run_cycle()

    kinds = [e[0] for e in rec.events]
    assert "order" in kinds, "a resting (unfilled) order must still produce a working-order alert"
    assert "trade" not in kinds, "nothing filled, so no fill alert should be sent"


def test_within_cycle_exposure_cap_with_async_fills(config, tmp_path, monkeypatch):
    # With async (resting) fills, several entries in ONE cycle must still not
    # collectively exceed max_total_exposure_usd, even though no fill has
    # reconciled yet. maxpos*concurrency (50*5=250) deliberately exceeds the
    # 120 total cap, so a naive loop would over-commit.
    config["mode"]["paper_trading"] = True
    config["mode"]["paper_data_source"] = "sim"
    config["mode"]["sim_markets"] = 8
    config["loop"]["state_file"] = "state.json"
    config["risk"]["max_position_usd"] = 50
    config["risk"]["max_total_exposure_usd"] = 120
    config["risk"]["max_concurrent_positions"] = 5
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("FORCE_PAPER", "true")
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    settings = config_mod.load_settings("config.yaml")

    t = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    app = App(settings, FixedClock(t), persist=False)
    app.executor.broker = RestingBroker(10_000.0)   # cash is not the constraint
    app.notifier = RecordingNotifier()

    app.run_cycle()

    buys = [o for o in app.executor.broker.created if o.action == "buy"]
    total_cost = sum(o.count * o.price_cents / 100 for o in buys)
    assert buys, "expected at least one entry"
    assert total_cost <= 120 + 1e-6, f"committed ${total_cost:.2f} exceeds the $120 total cap"


def test_error_breaker_keeps_stops_and_self_clears(config, tmp_path, monkeypatch):
    # C1: a tripped error breaker must NOT abandon protective stops, and a healthy
    # cycle must clear it (no permanent latch requiring a manual restart).
    app = _boot(config, tmp_path, monkeypatch)
    ds = StaticDS(bid=90)                       # 90 <= stop_loss 93 -> should exit
    app.data_source = ds
    app.executor.data_source = ds
    app.executor.broker.positions["M"] = Position("M", 10, 98)
    app.risk.consecutive_errors = app.risk.max_consecutive_errors   # breaker tripped
    assert app.risk.circuit_broken() is True
    assert app.risk.global_halt() is False     # breaker no longer freezes everything

    app.run_cycle()

    assert "M" not in app.executor.broker.positions, "stop must fire even with breaker tripped"
    assert app.risk.consecutive_errors == 0, "a healthy cycle must clear the breaker"


def test_no_bid_position_marks_at_last_bid_not_cost(config, tmp_path, monkeypatch):
    # C4: a held position that loses its bid must be marked off the LAST seen bid
    # (so a slide toward 0 shows in equity), not flattered back to cost basis.
    app = _boot(config, tmp_path, monkeypatch)
    ds = StaticDS(bid=95)                       # above the stop, so it isn't sold
    app.data_source = ds
    app.executor.data_source = ds
    app.executor.broker.balance_usd = 0.0
    app.executor.broker.positions["M"] = Position("M", 100, 98)

    app.run_cycle()                             # sees bid 95 -> remembers it
    assert app.risk.last_equity == pytest.approx(95.0, abs=1.0)

    ds.bid = None                               # market goes no-bid (gapped/illiquid)
    ds.ask = None
    app.run_cycle()
    # Marked at the last bid (95), NOT the $98 cost basis that would hide the loss.
    assert app.risk.last_equity == pytest.approx(95.0, abs=1.0)


def test_loop_daily_limit_halts_entries(config, tmp_path, monkeypatch):
    app = _boot(config, tmp_path, monkeypatch)
    # Force the daily loss latch on, then ensure a cycle opens no new positions.
    app.risk.update_daily_pnl(1000.0)
    app.risk.update_daily_pnl(800.0)               # -20% -> trips
    assert app.risk.daily_limit_hit is True
    app.run_cycle()
    # flatten_on_daily_limit is true in the base config, so it flattens but must NOT
    # open any buys; no position should remain open.
    assert app.executor.broker.positions == {}
