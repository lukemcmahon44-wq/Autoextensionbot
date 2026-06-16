"""Order execution + reconciliation.

Two brokers behind one interface:
  * ``LiveBroker``  -- sends real limit orders to Kalshi and reads the real
    balance/positions back.
  * ``PaperBroker`` -- simulates fills locally against the current top-of-book.
    It NEVER calls a real-money endpoint, dedupes by ``client_order_id`` (so a
    retried intent can't double-fill), and is the data source of truth in paper
    mode.

Idempotency: every order carries a ``client_order_id``. The network layer
retries with the *same* body, Kalshi dedupes on that id server-side, and the
paper broker dedupes on it locally. A retry therefore can never double-fill.
"""
from __future__ import annotations

import logging
from typing import Dict, Mapping, Optional

from .models import (
    AccountState,
    EntryPlan,
    Market,
    OrderRequest,
    OrderResult,
    Position,
    cents_to_usd,
)
from .strategy import ExitDecision

log = logging.getLogger("kalshi.executor")


def make_client_order_id(action: str, ticker: str, seq: int, day: str = "") -> str:
    """Deterministic, unique-per-intent idempotency key.

    Same (action, ticker, seq, day) -> same id, so a retry of the *same* intent
    reuses it; a different intent (next seq) gets a different id.
    """
    base = f"ks-{day}-{action}-{ticker}-{seq}" if day else f"ks-{action}-{ticker}-{seq}"
    return base.replace("/", "_")


# --------------------------------------------------------------------------
class LiveBroker:
    def __init__(self, client):
        self.client = client

    def get_account(self) -> AccountState:
        balance = self.client.get_balance_usd()
        positions: Dict[str, Position] = {}
        for mp in self.client.get_positions_raw():
            count = int(mp.get("position", 0))
            if count <= 0:
                # YES-only bot; ignore flat/NO exposure but it remains visible on
                # the exchange (and in logs) -- we just don't manage it here.
                continue
            exposure_cents = abs(int(mp.get("market_exposure", 0)))
            avg = int(round(exposure_cents / count)) if count else 0
            positions[mp["ticker"]] = Position(mp["ticker"], count, max(1, min(99, avg)))
        return AccountState(balance_usd=balance, positions=positions)

    def create_order(self, order: OrderRequest, market: Optional[Market]) -> OrderResult:
        try:
            resp = self.client.create_order(order)
        except Exception as exc:  # noqa: BLE001 -- surfaced to caller/circuit breaker
            return OrderResult(ok=False, error=str(exc))
        return OrderResult(
            ok=True,
            order_id=str(resp.get("order_id") or resp.get("id") or ""),
            # Resting/partial fills are reflected on the next reconcile, not assumed here.
            filled_count=int(resp.get("filled_count", 0) or 0),
            avg_fill_cents=resp.get("yes_price"),
        )

    def settle_closed(self, data_source, now: float) -> None:
        # Live settlement is the exchange's job and shows up on reconcile.
        return None


# --------------------------------------------------------------------------
class PaperBroker:
    """Simulates fills against the current book. No real-money endpoint, ever."""

    def __init__(self, starting_balance_usd: float = 1000.0):
        self.balance_usd = float(starting_balance_usd)
        self.positions: Dict[str, Position] = {}
        self._seen: Dict[str, OrderResult] = {}  # client_order_id -> result (idempotency)

    def get_account(self) -> AccountState:
        # Return a copy so callers can't mutate broker state by accident.
        return AccountState(
            balance_usd=self.balance_usd,
            positions={t: Position(p.ticker, p.count, p.avg_price_cents) for t, p in self.positions.items()},
        )

    def create_order(self, order: OrderRequest, market: Optional[Market]) -> OrderResult:
        if order.client_order_id in self._seen:
            log.info("paper: duplicate client_order_id %s ignored (idempotent)", order.client_order_id)
            return self._seen[order.client_order_id]

        if market is None or market.yes_bid is None or market.yes_ask is None:
            result = OrderResult(ok=False, error="no book to simulate against")
            self._seen[order.client_order_id] = result
            return result

        if order.action == "buy":
            result = self._fill_buy(order, market)
        else:
            result = self._fill_sell(order, market)
        self._seen[order.client_order_id] = result
        return result

    def _fill_buy(self, order: OrderRequest, market: Market) -> OrderResult:
        # A buy limit fills only if marketable (limit >= best ask).
        if order.price_cents < market.yes_ask:
            return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=0)
        fill_price = market.yes_ask
        fill_count = min(order.count, market.yes_ask_depth or order.count)
        cost = fill_count * cents_to_usd(fill_price)
        if cost > self.balance_usd:  # affordability clamp
            fill_count = int(self.balance_usd / cents_to_usd(fill_price))
            cost = fill_count * cents_to_usd(fill_price)
        if fill_count <= 0:
            return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=0)

        self.balance_usd -= cost
        existing = self.positions.get(order.ticker)
        if existing:
            total = existing.count + fill_count
            avg = round((existing.cost_usd + cost) / cents_to_usd(1) / total)
            self.positions[order.ticker] = Position(order.ticker, total, max(1, min(99, avg)))
        else:
            self.positions[order.ticker] = Position(order.ticker, fill_count, fill_price)
        log.info("paper BUY %s x%d @ %dc (cost $%.2f)", order.ticker, fill_count, fill_price, cost)
        return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=fill_count, avg_fill_cents=fill_price)

    def _fill_sell(self, order: OrderRequest, market: Market) -> OrderResult:
        pos = self.positions.get(order.ticker)
        if not pos:
            return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=0)
        # A sell limit fills only if marketable (limit <= best bid).
        if order.price_cents > market.yes_bid:
            return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=0)
        fill_price = market.yes_bid
        fill_count = min(order.count, pos.count, market.yes_bid_depth or order.count)
        if fill_count <= 0:
            return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=0)
        proceeds = fill_count * cents_to_usd(fill_price)
        self.balance_usd += proceeds
        remaining = pos.count - fill_count
        if remaining > 0:
            self.positions[order.ticker] = Position(order.ticker, remaining, pos.avg_price_cents)
        else:
            del self.positions[order.ticker]
        log.info("paper SELL %s x%d @ %dc (proceeds $%.2f)", order.ticker, fill_count, fill_price, proceeds)
        return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=fill_count, avg_fill_cents=fill_price)

    def settle_closed(self, data_source, now: float) -> None:
        """Settle any held market that has closed at its binary value (0 or 100)."""
        for ticker in list(self.positions.keys()):
            value = data_source.settlement_value_cents(ticker, now)
            if value is None:
                continue
            pos = self.positions.pop(ticker)
            proceeds = pos.count * cents_to_usd(value)
            self.balance_usd += proceeds
            log.info("paper SETTLE %s x%d @ %dc -> $%.2f", ticker, pos.count, value, proceeds)

    # paper state persistence
    def snapshot(self) -> dict:
        return {
            "balance_usd": self.balance_usd,
            "positions": {t: vars(p) for t, p in self.positions.items()},
            "seen": list(self._seen.keys()),
        }

    def restore(self, snap: Mapping) -> None:
        if not snap:
            return
        self.balance_usd = float(snap.get("balance_usd", self.balance_usd))
        self.positions = {
            t: Position(d["ticker"], d["count"], d["avg_price_cents"])
            for t, d in (snap.get("positions") or {}).items()
        }
        self._seen = {cid: OrderResult(ok=True) for cid in (snap.get("seen") or [])}


# --------------------------------------------------------------------------
class Executor:
    """Wraps a broker with id generation, exits, flatten, and reconciliation."""

    def __init__(self, broker, data_source, strategy_cfg: Mapping, *, day_fn=lambda: ""):
        self.broker = broker
        self.data_source = data_source
        self.cfg = strategy_cfg
        self._seq = 0
        self._day_fn = day_fn

    def _next_id(self, action: str, ticker: str) -> str:
        self._seq += 1
        return make_client_order_id(action, ticker, self._seq, self._day_fn())

    # ----- reconciliation -------------------------------------------------
    def reconcile(self, now: float) -> AccountState:
        """Exchange (or paper broker) is the source of truth. Settle then read."""
        self.broker.settle_closed(self.data_source, now)
        return self.broker.get_account()

    # ----- entries / exits ------------------------------------------------
    def place_entry(self, plan: EntryPlan, market: Market) -> OrderResult:
        order = OrderRequest(
            ticker=plan.ticker,
            action="buy",
            side="yes",
            price_cents=plan.limit_price_cents,
            count=plan.count,
            client_order_id=self._next_id("buy", plan.ticker),
        )
        return self.broker.create_order(order, market)

    def place_exit(self, decision: ExitDecision, position: Position, market: Market) -> OrderResult:
        order = OrderRequest(
            ticker=position.ticker,
            action="sell",
            side="yes",
            price_cents=decision.limit_price_cents,
            count=decision.count,
            client_order_id=self._next_id("sell", position.ticker),
        )
        return self.broker.create_order(order, market)

    def flatten_all(self, account: AccountState, market_by_ticker: Mapping[str, Market]) -> None:
        """Sell every open position to close with a marketable limit. Limit only."""
        slippage = self.cfg["max_exit_slippage_cents"]
        for ticker, pos in list(account.positions.items()):
            market = market_by_ticker.get(ticker)
            if market is None or market.yes_bid is None:
                log.warning("flatten: no live bid for %s; cannot sell this cycle", ticker)
                continue
            limit = max(1, market.yes_bid - slippage)
            order = OrderRequest(
                ticker=ticker,
                action="sell",
                side="yes",
                price_cents=limit,
                count=pos.count,
                client_order_id=self._next_id("flat", ticker),
            )
            res = self.broker.create_order(order, market)
            log.info("flatten %s: filled %d", ticker, res.filled_count)
