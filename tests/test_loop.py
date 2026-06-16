"""Integration tests for the wired loop in offline paper/sim mode."""
import os

import yaml

from src import config as config_mod
from src.main import App, Clock


def _boot(config, tmp_path, monkeypatch, scale=1.0):
    config["mode"]["paper_trading"] = True
    config["mode"]["paper_data_source"] = "sim"   # force offline simulator
    config["loop"]["state_file"] = "state.json"
    monkeypatch.chdir(tmp_path)                    # isolate state.json / KILL
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setenv("FORCE_PAPER", "true")
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    settings = config_mod.load_settings("config.yaml")
    return App(settings, Clock(scale=scale))


def test_loop_runs_and_persists(config, tmp_path, monkeypatch):
    app = _boot(config, tmp_path, monkeypatch, scale=3000)
    for _ in range(8):
        app.run_cycle()                            # must not raise
    assert os.path.exists("state.json")
    assert app.executor.broker.balance_usd >= 0
    # The seeded simulator always presents at least one in-band market early on.
    # By now we should have transacted at least once (an entry or a settlement).
    assert app.executor._seq >= 1


def test_loop_kill_switch_flattens(config, tmp_path, monkeypatch):
    app = _boot(config, tmp_path, monkeypatch, scale=1.0)  # no settlement, positions persist
    app.run_cycle()
    assert app.executor.broker.positions, "expected at least one open position after a cycle"
    # Drop the kill switch and run one more cycle.
    (tmp_path / "KILL").write_text("halt")
    app.run_cycle()
    assert app.executor.broker.positions == {}, "kill switch must flatten within one cycle"


def test_loop_daily_limit_halts_entries(config, tmp_path, monkeypatch):
    app = _boot(config, tmp_path, monkeypatch, scale=1.0)
    # Force the daily loss latch on, then ensure a cycle opens no new positions.
    app.risk.update_daily_pnl(1000.0)
    app.risk.update_daily_pnl(800.0)               # -20% -> trips
    assert app.risk.daily_limit_hit is True
    before = app.executor._seq
    app.run_cycle()
    # flatten_on_daily_limit is true in the base config, so it flattens (a SELL may
    # bump the seq) but must NOT open any buys; no position should remain open.
    assert app.executor.broker.positions == {}
