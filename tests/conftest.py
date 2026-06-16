"""Shared test fixtures/config. No network, no real credentials."""
import copy

import pytest

BASE_CONFIG = {
    "mode": {
        "paper_trading": True,
        "api_base_live": "https://api.elections.kalshi.com/trade-api/v2",
        "api_base_demo": "https://demo-api.kalshi.co/trade-api/v2",
        "paper_data_source": "sim",
    },
    "strategy": {
        "max_hours_to_close": 8,
        "entry_min_cents": 96,
        "entry_max_cents": 99,
        "max_entry_spread_cents": 2,
        "min_orderbook_depth_contracts": 50,
        "max_entry_slippage_cents": 1,
        "max_exit_slippage_cents": 3,
        "stop_loss_cents": 93,
        "take_profit_cents": None,
    },
    "risk": {
        "max_position_usd": 50,
        "max_total_exposure_usd": 250,
        "max_concurrent_positions": 3,
        "daily_loss_limit_pct": 5.0,
        "flatten_on_daily_limit": True,
        "no_new_entries_before_close_min": 10,
        "max_consecutive_errors": 5,
    },
    "kill_switch": {"file": "KILL", "flatten_on_kill": True},
    "loop": {"poll_interval_sec": 30, "reconcile_every_cycles": 10, "state_file": "state.json"},
    "logging": {"level": "INFO"},
}


@pytest.fixture
def config():
    return copy.deepcopy(BASE_CONFIG)
