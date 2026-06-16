"""Backtest harness tests (fully offline, deterministic)."""
import os

from src import config as config_mod
from src.backtest import run_backtest, simulate_day, summarize


def _settings():
    os.environ["FORCE_PAPER"] = "true"      # config ships live; force paper for analysis
    return config_mod.load_settings("config.yaml")


def test_backtest_runs_and_summarizes():
    results = run_backtest(_settings(), trials=12, start_balance=500, steps=8, gap_prob=0.2)
    assert len(results) == 12
    stats = summarize(results)
    assert stats["trials"] == 12
    assert stats["worst_day_pct"] <= stats["best_day_pct"]
    assert 0.0 <= stats["pct_days_profitable"] <= 100.0


def test_backtest_deterministic_per_seed():
    s = _settings()
    a = simulate_day(s, seed=42, start_balance=500, steps=8)
    b = simulate_day(s, seed=42, start_balance=500, steps=8)
    assert a.pnl == b.pnl
    assert a.orders == b.orders
    assert a.max_drawdown_pct == b.max_drawdown_pct


def test_gap_risk_makes_the_worst_day_worse():
    s = _settings()
    no_gap = summarize(run_backtest(s, trials=40, start_balance=500, steps=10, gap_prob=0.0))
    with_gap = summarize(run_backtest(s, trials=40, start_balance=500, steps=10, gap_prob=0.6))
    # Gaps only add downside (favorites that jump through the stop), so the worst
    # day with heavy gap risk must be at least as bad as without.
    assert with_gap["worst_day_pct"] <= no_gap["worst_day_pct"]


def test_daily_loss_cap_bounds_the_worst_day():
    s = _settings()
    # Even with very heavy gap risk, the daily loss limit (5%) + flatten should keep
    # the worst day from running far past the cap.
    stats = summarize(run_backtest(s, trials=40, start_balance=500, steps=12, gap_prob=0.8))
    assert stats["worst_day_pct"] > -15.0    # comfortably bounded, not a wipeout
