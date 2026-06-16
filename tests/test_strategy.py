"""Strategy tests: plan_entry sizing and should_exit stop/take-profit."""
from src.models import AccountState, Market, Position
from src.strategy import plan_entry, should_exit

STRAT = {
    "entry_min_cents": 96,
    "entry_max_cents": 99,
    "max_entry_slippage_cents": 1,
    "max_exit_slippage_cents": 3,
    "stop_loss_cents": 93,
    "take_profit_cents": None,
}


def market(ticker="M", bid=97, ask=98, ask_depth=500):
    return Market(ticker, "open", 0, bid, ask, ask_depth, 500)


def account(balance=1000.0, positions=None):
    return AccountState(balance_usd=balance, positions=positions or {})


# --- plan_entry sizing ----------------------------------------------------
def test_limit_price_applies_slippage_capped_at_band():
    plan = plan_entry(market(ask=98), account(), STRAT, max_position_usd=50, remaining_exposure_usd=1000)
    assert plan.limit_price_cents == 99   # 98 + 1 slippage, still <= entry_max 99


def test_limit_price_never_exceeds_entry_max():
    plan = plan_entry(market(ask=99), account(), STRAT, max_position_usd=50, remaining_exposure_usd=1000)
    assert plan.limit_price_cents == 99   # min(99+1, 99)


def test_size_capped_by_position_budget():
    plan = plan_entry(market(ask=98), account(), STRAT, max_position_usd=50, remaining_exposure_usd=1000)
    # limit 99c -> 0.99/contract -> floor(50/0.99)=50
    assert plan.count == 50
    assert plan.cost_usd <= 50 + 1e-9


def test_size_capped_by_remaining_exposure():
    plan = plan_entry(market(ask=98), account(), STRAT, max_position_usd=50, remaining_exposure_usd=10)
    assert plan.count == int(10 / 0.99)


def test_size_capped_by_ask_depth():
    plan = plan_entry(market(ask=98, ask_depth=7), account(), STRAT, max_position_usd=50, remaining_exposure_usd=1000)
    assert plan.count == 7


def test_existing_position_reduces_room():
    held = {"M": Position("M", count=40, avg_price_cents=99)}  # cost $39.60
    plan = plan_entry(market(ask=98), account(positions=held), STRAT,
                      max_position_usd=50, remaining_exposure_usd=1000)
    # room = 50 - 39.60 = 10.40 -> floor(10.40/0.99) = 10
    assert plan.count == 10


def test_no_plan_when_no_budget():
    assert plan_entry(market(ask=98), account(balance=0), STRAT,
                      max_position_usd=50, remaining_exposure_usd=1000) is None


def test_no_plan_when_no_ask():
    assert plan_entry(market(ask=None), account(), STRAT,
                      max_position_usd=50, remaining_exposure_usd=1000) is None


# --- should_exit ----------------------------------------------------------
def test_stop_loss_triggers_at_threshold():
    pos = Position("M", 50, 98)
    d = should_exit(pos, market(bid=93), STRAT)
    assert d is not None and d.reason == "stop_loss"
    assert d.limit_price_cents == 90       # 93 - 3 slippage
    assert d.count == 50


def test_stop_loss_triggers_below_threshold():
    d = should_exit(Position("M", 10, 98), market(bid=80), STRAT)
    assert d is not None and d.reason == "stop_loss"


def test_no_exit_in_safe_zone():
    assert should_exit(Position("M", 10, 98), market(bid=97), STRAT) is None


def test_take_profit_none_holds_winner():
    assert should_exit(Position("M", 10, 98), market(bid=99), STRAT) is None


def test_take_profit_triggers_when_set():
    cfg = dict(STRAT, take_profit_cents=98)
    d = should_exit(Position("M", 10, 96), market(bid=98), cfg)
    assert d is not None and d.reason == "take_profit"
    assert d.limit_price_cents == 95       # 98 - 3 slippage


def test_no_exit_when_no_bid():
    assert should_exit(Position("M", 10, 98), market(bid=None), STRAT) is None
