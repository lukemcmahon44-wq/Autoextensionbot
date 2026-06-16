"""Tests for the demo smoke runner (with a fake client -- no network)."""
from types import SimpleNamespace

from src.smoke import run_smoke


class FakeClient:
    def __init__(self, markets=None, fail=None):
        self.markets = markets if markets is not None else [{"ticker": "T1"}]
        self.fail = fail or set()
        self.created = []
        self.cancelled = []

    def get_exchange_status(self):
        if "status" in self.fail:
            raise RuntimeError("503 unavailable")
        return {"exchange_active": True}

    def get_balance_usd(self):
        if "balance" in self.fail:
            raise RuntimeError("401 unauthorized")
        return 100.0

    def list_all_markets(self, **kwargs):
        return self.markets

    def get_orderbook(self, ticker):
        return {"yes": [[97, 100]], "no": [[2, 100]]}

    def get_positions_raw(self):
        return []

    def create_order(self, order):
        self.created.append(order)
        return {"order_id": "oid-1"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)


def settings(api_base="https://demo-api.kalshi.co/trade-api/v2"):
    return SimpleNamespace(
        strategy={"max_hours_to_close": 8},
        api_base=api_base,
        paper=True,
        secrets=SimpleNamespace(api_key_id="x", private_key_path="y"),
    )


def test_smoke_all_checks_pass():
    c = FakeClient()
    assert run_smoke(c, settings()) is True


def test_smoke_reports_failure():
    c = FakeClient(fail={"balance"})
    assert run_smoke(c, settings()) is False


def test_smoke_passes_with_no_markets():
    # No open markets in the window is a warning, not a failure.
    c = FakeClient(markets=[])
    assert run_smoke(c, settings()) is True


def test_order_roundtrip_on_demo_creates_and_cancels():
    c = FakeClient()
    assert run_smoke(c, settings(), place_test_order=True) is True
    assert len(c.created) == 1
    assert c.created[0].price_cents == 1 and c.created[0].count == 1
    assert c.cancelled == ["oid-1"]


def test_order_roundtrip_refuses_non_demo_host():
    c = FakeClient()
    ok = run_smoke(c, settings(api_base="https://api.elections.kalshi.com/trade-api/v2"),
                   place_test_order=True)
    assert ok is False              # the order check fails...
    assert c.created == []          # ...and crucially places NO order on a live host
