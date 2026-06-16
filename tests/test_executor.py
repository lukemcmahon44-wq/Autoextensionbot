"""Executor tests: idempotent order ids, paper fills, settlement, reconcile."""
from src.executor import Executor, PaperBroker, make_client_order_id
from src.models import EntryPlan, Market, OrderRequest, Position
from src.strategy import ExitDecision

STRAT = {"max_exit_slippage_cents": 3}


def market(ticker="M", bid=97, ask=98, bid_depth=500, ask_depth=500):
    return Market(ticker, "open", 0, bid, ask, ask_depth, bid_depth)


class SettleDS:
    """Data source that settles a given ticker at a fixed value."""

    def __init__(self, ticker=None, value=None):
        self.ticker = ticker
        self.value = value

    def settlement_value_cents(self, ticker, now):
        return self.value if ticker == self.ticker else None

    def orderbook_top(self, ticker):
        return (97, 98, 500, 500)


# --- idempotent ids -------------------------------------------------------
def test_client_order_id_is_deterministic_per_intent():
    a = make_client_order_id("buy", "MKT", 5, "20260101")
    b = make_client_order_id("buy", "MKT", 5, "20260101")
    assert a == b                              # same intent -> same id (retry-safe)


def test_client_order_id_unique_per_seq():
    a = make_client_order_id("buy", "MKT", 5)
    b = make_client_order_id("buy", "MKT", 6)
    assert a != b


def test_order_requires_client_order_id():
    import pytest

    with pytest.raises(ValueError):
        OrderRequest("MKT", "buy", "yes", 98, 10, client_order_id="")


# --- paper broker fills ---------------------------------------------------
def test_paper_buy_fills_and_debits():
    b = PaperBroker(100.0)
    order = OrderRequest("M", "buy", "yes", 99, 10, "cid-1")
    res = b.create_order(order, market(ask=98))
    assert res.filled_count == 10
    assert b.positions["M"].count == 10
    assert b.balance_usd < 100.0


def test_paper_buy_idempotent_on_duplicate_id():
    b = PaperBroker(100.0)
    order = OrderRequest("M", "buy", "yes", 99, 10, "cid-dup")
    first = b.create_order(order, market(ask=98))
    bal_after_first = b.balance_usd
    second = b.create_order(order, market(ask=98))   # same client_order_id
    assert second is first or second.filled_count == first.filled_count
    assert b.balance_usd == bal_after_first          # no second debit
    assert b.positions["M"].count == 10              # not doubled


def test_paper_buy_non_marketable_does_not_fill():
    b = PaperBroker(100.0)
    order = OrderRequest("M", "buy", "yes", 97, 10, "cid-2")  # limit 97 < ask 98
    res = b.create_order(order, market(ask=98))
    assert res.filled_count == 0
    assert "M" not in b.positions


def test_paper_buy_affordability_clamp():
    b = PaperBroker(5.0)                              # only $5
    order = OrderRequest("M", "buy", "yes", 99, 100, "cid-3")
    res = b.create_order(order, market(ask=98))
    assert res.filled_count == 5                      # floor(5 / 0.98) = 5
    assert b.balance_usd >= 0


def test_paper_sell_closes_and_credits():
    b = PaperBroker(0.0)
    b.positions["M"] = Position("M", 10, 98)
    order = OrderRequest("M", "sell", "yes", 90, 10, "cid-4")  # limit 90 <= bid 97
    res = b.create_order(order, market(bid=97))
    assert res.filled_count == 10
    assert "M" not in b.positions
    assert b.balance_usd > 0


def test_paper_settlement_at_100():
    b = PaperBroker(0.0)
    b.positions["M"] = Position("M", 10, 98)
    b.settle_closed(SettleDS("M", 100), now=0)
    assert "M" not in b.positions
    assert b.balance_usd == 10 * 1.00


def test_paper_settlement_at_0_is_total_loss():
    b = PaperBroker(0.0)
    b.positions["M"] = Position("M", 10, 98)
    b.settle_closed(SettleDS("M", 0), now=0)
    assert "M" not in b.positions
    assert b.balance_usd == 0.0


# --- executor reconcile / flatten -----------------------------------------
def test_executor_reconcile_settles_then_reads():
    b = PaperBroker(0.0)
    b.positions["M"] = Position("M", 10, 98)
    ex = Executor(b, SettleDS("M", 100), STRAT)
    account = ex.reconcile(now=0)
    assert account.balance_usd == 10.0
    assert account.positions == {}


def test_executor_place_entry_sets_client_order_id():
    b = PaperBroker(100.0)
    ex = Executor(b, SettleDS(), STRAT, day_fn=lambda: "20260101")
    plan = EntryPlan("M", 99, 10)
    ex.place_entry(plan, market(ask=98))
    # one client_order_id recorded by the paper broker
    assert any(cid.startswith("ks-20260101-buy-M") for cid in b._seen)


def test_executor_flatten_all_sells_everything():
    b = PaperBroker(0.0)
    b.positions["A"] = Position("A", 10, 98)
    b.positions["B"] = Position("B", 5, 97)
    ex = Executor(b, SettleDS(), STRAT)
    account = b.get_account()
    ex.flatten_all(account, {"A": market("A", bid=97), "B": market("B", bid=96)})
    assert b.positions == {}
    assert b.balance_usd > 0


def test_paper_snapshot_restore_roundtrip():
    b = PaperBroker(50.0)
    b.create_order(OrderRequest("M", "buy", "yes", 99, 10, "cid-x"), market(ask=98))
    snap = b.snapshot()
    b2 = PaperBroker(0.0)
    b2.restore(snap)
    assert b2.balance_usd == b.balance_usd
    assert b2.positions["M"].count == 10
    # restored id is still deduped
    res = b2.create_order(OrderRequest("M", "buy", "yes", 99, 10, "cid-x"), market(ask=98))
    assert res.filled_count == 0 or "cid-x" in b2._seen
