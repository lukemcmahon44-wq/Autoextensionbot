"""Safety-layer tests. These are guardrail tests: do not weaken them.

If a change here is needed because behavior legitimately changed, the bar is
that the new behavior is *at least as safe* as the old one.
"""


from src.models import AccountState, EntryPlan, Market, Position
from src.risk import RiskManager

FIXED_NOW = 1_700_000_000.0  # arbitrary fixed "now" for deterministic tests


def make_rm(config, now=FIXED_NOW):
    return RiskManager(config, now_fn=lambda: now)


def open_market(seconds_to_close=3600):
    return Market(
        ticker="TEST-MKT",
        status="open",
        close_ts=int(FIXED_NOW + seconds_to_close),
        yes_bid=97,
        yes_ask=98,
        yes_ask_depth=500,
        yes_bid_depth=500,
    )


def plan(ticker="TEST-MKT", price=98, count=10):
    return EntryPlan(ticker=ticker, limit_price_cents=price, count=count)


def account(balance=1000.0, positions=None):
    return AccountState(balance_usd=balance, positions=positions or {})


# --- kill switch ----------------------------------------------------------
def test_kill_switch_blocks_entries(config, tmp_path):
    kill = tmp_path / "KILL"
    config["kill_switch"]["file"] = str(kill)
    rm = make_rm(config)
    assert rm.check_entry(plan(), account(), open_market()).allowed is True
    kill.write_text("halt")
    assert rm.kill_switch_active() is True
    decision = rm.check_entry(plan(), account(), open_market())
    assert decision.allowed is False
    assert "kill switch" in decision.reason


def test_kill_switch_requests_flatten(config, tmp_path):
    kill = tmp_path / "KILL"
    config["kill_switch"]["file"] = str(kill)
    config["kill_switch"]["flatten_on_kill"] = True
    rm = make_rm(config)
    kill.write_text("x")
    assert rm.should_flatten() is True


def test_kill_switch_no_flatten_when_disabled(config, tmp_path):
    kill = tmp_path / "KILL"
    config["kill_switch"]["file"] = str(kill)
    config["kill_switch"]["flatten_on_kill"] = False
    rm = make_rm(config)
    kill.write_text("x")
    assert rm.halt_reason() is not None      # still halts entries
    assert rm.should_flatten() is False       # but does not force a flatten


# --- daily loss limit -----------------------------------------------------
def test_daily_loss_limit_trips_and_halts(config):
    rm = make_rm(config)
    rm.update_daily_pnl(1000.0)               # start of day baseline
    assert rm.halt_reason() is None
    rm.update_daily_pnl(960.0)                # -4% -> still ok
    assert rm.daily_limit_hit is False
    rm.update_daily_pnl(949.0)                # -5.1% -> trips
    assert rm.daily_limit_hit is True
    assert "daily loss limit" in rm.halt_reason()
    assert rm.should_flatten() is True        # flatten_on_daily_limit default true


def test_daily_limit_latches_even_if_equity_recovers(config):
    rm = make_rm(config)
    rm.update_daily_pnl(1000.0)
    rm.update_daily_pnl(900.0)                # -10% trips
    assert rm.daily_limit_hit is True
    rm.update_daily_pnl(1000.0)               # recovery same day must NOT un-halt
    assert rm.daily_limit_hit is True


def test_daily_baseline_resets_next_day(config):
    rm = RiskManager(config, now_fn=lambda: FIXED_NOW)
    rm.update_daily_pnl(1000.0)
    rm.update_daily_pnl(900.0)
    assert rm.daily_limit_hit is True
    # advance ~1 day
    rm._now = lambda: FIXED_NOW + 86400
    rm.update_daily_pnl(900.0)                # new day, fresh baseline
    assert rm.daily_limit_hit is False


# --- exposure / position / concurrency caps -------------------------------
def test_position_size_cap(config):
    rm = make_rm(config)
    # 60 contracts * $0.98 = $58.80 > $50 cap
    decision = rm.check_entry(plan(count=60), account(), open_market())
    assert decision.allowed is False
    assert "position cost" in decision.reason


def test_position_cap_accounts_for_existing(config):
    rm = make_rm(config)
    held = {"TEST-MKT": Position("TEST-MKT", count=40, avg_price_cents=98)}  # $39.20
    # adding 20 * $0.98 = $19.60 -> total $58.80 > $50
    decision = rm.check_entry(plan(count=20), account(positions=held), open_market())
    assert decision.allowed is False


def test_total_exposure_cap(config):
    rm = make_rm(config)
    positions = {
        f"M{i}": Position(f"M{i}", count=50, avg_price_cents=98) for i in range(5)
    }  # 5 * $49 = $245 already; but concurrency would block first -- bump cap
    config["risk"]["max_concurrent_positions"] = 99
    rm = make_rm(config)
    decision = rm.check_entry(plan(ticker="NEW", count=10), account(positions=positions), open_market())
    assert decision.allowed is False
    assert "exposure" in decision.reason


def test_concurrency_cap_blocks_new_ticker(config):
    rm = make_rm(config)
    positions = {
        f"M{i}": Position(f"M{i}", count=1, avg_price_cents=98) for i in range(3)
    }
    decision = rm.check_entry(plan(ticker="NEW", count=1), account(positions=positions), open_market())
    assert decision.allowed is False
    assert "max_concurrent_positions" in decision.reason


def test_concurrency_cap_allows_adding_to_existing(config):
    rm = make_rm(config)
    positions = {
        "A": Position("A", count=1, avg_price_cents=98),
        "B": Position("B", count=1, avg_price_cents=98),
        "C": Position("C", count=1, avg_price_cents=98),
    }
    # at the cap, but adding to an existing ticker consumes no new slot
    decision = rm.check_entry(plan(ticker="A", count=1), account(positions=positions), open_market())
    assert decision.allowed is True


# --- pre-close cutoff -----------------------------------------------------
def test_no_entries_in_final_minutes(config):
    rm = make_rm(config)
    # close in 5 minutes, cutoff is 10 minutes
    decision = rm.check_entry(plan(), account(), open_market(seconds_to_close=300))
    assert decision.allowed is False
    assert "min of close" in decision.reason


def test_entries_allowed_outside_cutoff(config):
    rm = make_rm(config)
    decision = rm.check_entry(plan(), account(), open_market(seconds_to_close=1200))
    assert decision.allowed is True


# --- affordability --------------------------------------------------------
def test_insufficient_balance_blocks(config):
    rm = make_rm(config)
    decision = rm.check_entry(plan(count=10), account(balance=5.0), open_market())
    assert decision.allowed is False
    assert "balance" in decision.reason


# --- error circuit breaker ------------------------------------------------
def test_consecutive_error_breaker(config):
    rm = make_rm(config)
    for _ in range(4):
        assert rm.record_error() is False
    assert rm.record_error() is True          # 5th error trips
    assert rm.circuit_broken() is True
    assert "circuit breaker" in rm.halt_reason()
    # blocks entries
    assert rm.check_entry(plan(), account(), open_market()).allowed is False
    rm.record_success()                       # one good cycle clears it
    assert rm.circuit_broken() is False


# --- daily order throttle -------------------------------------------------
def test_daily_order_cap_blocks_entries(config):
    config["risk"]["max_orders_per_day"] = 2
    rm = make_rm(config)
    rm.update_daily_pnl(1000.0)
    assert rm.orders_exhausted() is False
    rm.record_order()
    rm.record_order()
    assert rm.orders_exhausted() is True
    assert "order cap" in rm.halt_reason()
    assert rm.check_entry(plan(), account(), open_market()).allowed is False


def test_daily_order_cap_zero_means_unlimited(config):
    config["risk"]["max_orders_per_day"] = 0
    rm = make_rm(config)
    for _ in range(1000):
        rm.record_order()
    assert rm.orders_exhausted() is False


def test_order_cap_resets_next_day(config):
    config["risk"]["max_orders_per_day"] = 1
    rm = make_rm(config)
    rm.update_daily_pnl(1000.0)
    rm.record_order()
    assert rm.orders_exhausted() is True
    rm._now = lambda: FIXED_NOW + 86400
    rm.update_daily_pnl(1000.0)               # new day -> fresh order budget
    assert rm.orders_exhausted() is False


# --- persistence ----------------------------------------------------------
def test_snapshot_restore_roundtrip(config):
    rm = make_rm(config)
    rm.update_daily_pnl(1000.0)
    rm.record_error()
    rm.record_order()
    snap = rm.snapshot()
    rm2 = make_rm(config)
    rm2.restore(snap)
    assert rm2.consecutive_errors == 1
    assert rm2.day_start_equity == 1000.0
    assert rm2.day_key == snap["day_key"]
    assert rm2.orders_today == 1
