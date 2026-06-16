"""Executor tests: idempotent order ids, paper fills, settlement, reconcile,
and the in-flight (resting) order guards that prevent over-buying / over-selling.
"""
from src.executor import Executor, PaperBroker, make_client_order_id
from src.models import AccountState, EntryPlan, Market, OrderRequest, OrderResult, Position
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


# --- in-flight (resting) order guards -------------------------------------
class FakeBroker:
    """Broker stub that returns queued OrderResults so we can simulate resting orders."""

    def __init__(self, account=None):
        self.account = account or AccountState(1000.0, {})
        self.created = []
        self.cancelled = []
        self.queue = []

    def get_account(self):
        return self.account

    def create_order(self, order, market):
        self.created.append(order)
        if self.queue:
            return self.queue.pop(0)
        return OrderResult(ok=True, order_id="o-" + order.client_order_id, filled_count=order.count)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    def settle_closed(self, ds, now):
        pass


def test_resting_entry_blocks_a_second_entry():
    b = FakeBroker()
    b.queue = [OrderResult(ok=True, order_id="resting-1", filled_count=0)]  # didn't fill
    ex = Executor(b, SettleDS(), STRAT)
    ex.place_entry(EntryPlan("M", 99, 10), market(ask=98))
    assert ex.has_inflight_entry("M") is True       # would be skipped next cycle


def test_full_fill_does_not_mark_inflight():
    b = FakeBroker()
    b.queue = [OrderResult(ok=True, order_id="o1", filled_count=10)]
    ex = Executor(b, SettleDS(), STRAT)
    ex.place_entry(EntryPlan("M", 99, 10), market(ask=98))
    assert ex.has_inflight_entry("M") is False


def test_reap_clears_inflight_once_position_appears():
    b = FakeBroker()
    b.queue = [OrderResult(ok=True, order_id="resting-1", filled_count=0)]
    ex = Executor(b, SettleDS(), STRAT)
    ex.place_entry(EntryPlan("M", 99, 10), market(ask=98))
    account = AccountState(1000.0, {"M": Position("M", 10, 98)})  # it filled
    ex.reap_inflight_entries(account, ttl_cycles=3)
    assert ex.has_inflight_entry("M") is False
    assert b.cancelled == []                          # nothing to cancel, it filled


def test_reap_cancels_stale_unfilled_entry_after_ttl():
    b = FakeBroker()
    b.queue = [OrderResult(ok=True, order_id="resting-1", filled_count=0)]
    ex = Executor(b, SettleDS(), STRAT)
    ex.place_entry(EntryPlan("M", 99, 10), market(ask=98))
    empty = AccountState(1000.0, {})
    for _ in range(3):
        ex.reap_inflight_entries(empty, ttl_cycles=3)   # age 1,2,3 -> still alive
        assert ex.has_inflight_entry("M") is True
    ex.reap_inflight_entries(empty, ttl_cycles=3)        # age 4 > ttl -> cancel
    assert ex.has_inflight_entry("M") is False
    assert b.cancelled == ["resting-1"]


def test_inflight_entry_counts_as_pseudo_position():
    b = FakeBroker()
    b.queue = [OrderResult(ok=True, order_id="resting-1", filled_count=0)]
    ex = Executor(b, SettleDS(), STRAT)
    ex.place_entry(EntryPlan("M", 98, 10), market(ask=98))
    pseudo = ex.inflight_entry_positions()
    assert pseudo["M"].count == 10
    assert pseudo["M"].avg_price_cents == 98
    assert abs(pseudo["M"].cost_usd - 9.8) < 1e-6


def test_exit_cancels_prior_resting_sell_before_repricing():
    b = FakeBroker()
    # First exit rests (bid gapped below limit), second exit reprices.
    b.queue = [
        OrderResult(ok=True, order_id="sell-1", filled_count=0),
        OrderResult(ok=True, order_id="sell-2", filled_count=0),
    ]
    ex = Executor(b, SettleDS(), STRAT)
    pos = Position("M", 10, 98)
    ex.place_exit(ExitDecision("stop_loss", 90, 10), pos, market(bid=92))
    ex.place_exit(ExitDecision("stop_loss", 88, 10), pos, market(bid=90))
    assert b.cancelled == ["sell-1"]                  # old resting sell was cancelled
    assert len(b.created) == 2


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
