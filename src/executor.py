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
import uuid
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


def make_client_order_id(action: str, ticker: str, seq: int, day: str = "", nonce: str = "") -> str:
    """Unique-per-intent idempotency key.

    Same (action, ticker, seq, day, nonce) -> same id, so a network retry of the
    *same* intent reuses it. The per-run ``nonce`` ensures that if the process
    crashes after sending an order but before persisting ``_seq``, the next run's
    re-used seq can't alias the previous run's id (which Kalshi would dedupe,
    silently dropping the new order).
    """
    parts = ["ks"]
    if day:
        parts.append(day)
    if nonce:
        parts.append(nonce)
    parts += [action, ticker, str(seq)]
    return "-".join(parts).replace("/", "_")


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
            # NOTE: assumes Kalshi's `market_exposure` is cost-basis in cents.
            # Confirm against the API; an out-of-range average means that
            # assumption is wrong, so surface it rather than silently clamping.
            exposure_cents = abs(int(mp.get("market_exposure", 0)))
            avg = int(round(exposure_cents / count)) if count else 0
            if not 1 <= avg <= 99:
                log.warning("position %s: derived avg %dc out of 1..99 "
                            "(market_exposure=%s, count=%s) -- check field semantics",
                            mp["ticker"], avg, exposure_cents, count)
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

    def cancel_order(self, order_id: str) -> None:
        if not order_id:
            return
        try:
            self.client.cancel_order(order_id)
        except Exception as exc:  # noqa: BLE001 -- best effort; surfaced via logs
            log.warning("cancel_order %s failed (ignored): %s", order_id, exc)

    def settle_closed(self, data_source, now: float) -> None:
        # Live settlement is the exchange's job and shows up on reconcile.
        return None


# --------------------------------------------------------------------------
class PaperBroker:
    """Simulates fills against the current book. No real-money endpoint, ever."""

    def __init__(self, starting_balance_usd: float = 1000.0, fee_rate: float = 0.0):
        self.balance_usd = float(starting_balance_usd)
        self.positions: Dict[str, Position] = {}
        self._seen: Dict[str, OrderResult] = {}  # client_order_id -> result (idempotency)
        # Kalshi-style trading fee ~ fee_rate * price * (1 - price) per contract,
        # charged on each execution. 0.0 = no fees (default; demo/tests unchanged).
        self.fee_rate = float(fee_rate)

    def _fee_usd(self, count: int, price_cents: int) -> float:
        p = cents_to_usd(price_cents)
        return self.fee_rate * count * p * (1.0 - p)

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

        self.balance_usd -= cost + self._fee_usd(fill_count, fill_price)
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
        self.balance_usd += proceeds - self._fee_usd(fill_count, fill_price)
        remaining = pos.count - fill_count
        if remaining > 0:
            self.positions[order.ticker] = Position(order.ticker, remaining, pos.avg_price_cents)
        else:
            del self.positions[order.ticker]
        log.info("paper SELL %s x%d @ %dc (proceeds $%.2f)", order.ticker, fill_count, fill_price, proceeds)
        return OrderResult(ok=True, order_id=f"paper-{order.client_order_id}", filled_count=fill_count, avg_fill_cents=fill_price)

    def cancel_order(self, order_id: str) -> None:
        # Paper fills are immediate, so there is never a resting order to cancel.
        return None

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

    def __init__(self, broker, data_source, strategy_cfg: Mapping, *, day_fn=lambda: "", run_id=None):
        self.broker = broker
        self.data_source = data_source
        self.cfg = strategy_cfg
        self._seq = 0
        self._day_fn = day_fn
        # A nonce unique to this process run, mixed into every client_order_id so a
        # stale (un-persisted) _seq after a crash can't collide with a prior run.
        self._run_id = run_id or uuid.uuid4().hex[:8]
        # In-flight (placed but not yet confirmed filled) resting orders. These let
        # us avoid stacking duplicate entries / exits across cycles, which would
        # otherwise breach the per-position and total-exposure caps once they fill.
        self._inflight_entry: Dict[str, dict] = {}   # ticker -> {order_id, age}
        self._inflight_exit: Dict[str, str] = {}      # ticker -> order_id

    def _next_id(self, action: str, ticker: str) -> str:
        self._seq += 1
        return make_client_order_id(action, ticker, self._seq, self._day_fn(), self._run_id)

    def has_inflight_entry(self, ticker: str) -> bool:
        """True if we have an unfilled entry resting for this ticker (don't stack)."""
        return ticker in self._inflight_entry

    def has_inflight_exit(self, ticker: str) -> bool:
        """True if a sell for this ticker is already working (avoids alert spam)."""
        return ticker in self._inflight_exit

    def inflight_entry_positions(self) -> Dict[str, Position]:
        """Resting (unfilled) entries as pseudo-positions, so their committed cost
        counts toward the exposure/concurrency caps until they fill or are reaped."""
        return {
            t: Position(t, r["count"], r["price_cents"])
            for t, r in self._inflight_entry.items()
        }

    def reap_inflight_entries(self, account: AccountState, ttl_cycles: int) -> None:
        """Resolve tracked entries each cycle: clear filled ones, cancel stale ones.

        Call once per cycle after reconcile. If the position now exists the entry
        filled (caps govern any further adds); otherwise we age it and, past the
        TTL, cancel the resting order so it can't fill unexpectedly later.
        """
        for ticker in list(self._inflight_entry):
            if ticker in account.positions:
                self._inflight_entry.pop(ticker, None)
                continue
            rec = self._inflight_entry[ticker]
            rec["age"] += 1
            if rec["age"] > ttl_cycles:
                log.info("entry for %s unfilled after %d cycles; cancelling", ticker, ttl_cycles)
                self.broker.cancel_order(rec["order_id"])
                self._inflight_entry.pop(ticker, None)

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
        res = self.broker.create_order(order, market)
        # If it didn't fully fill it may be resting -- track it (with its size) so we
        # don't place a second entry for the same market next cycle and so its
        # committed cost still counts toward the caps.
        if res.ok and res.filled_count < plan.count and res.order_id:
            self._inflight_entry[plan.ticker] = {
                "order_id": res.order_id, "age": 0,
                "count": plan.count, "price_cents": plan.limit_price_cents,
            }
        else:
            self._inflight_entry.pop(plan.ticker, None)
        return res

    def place_exit(self, decision: ExitDecision, position: Position, market: Market) -> OrderResult:
        # Cancel any earlier resting sell for this ticker before repricing, so a
        # falling market can't leave several resting sells that oversell on a bounce.
        prior = self._inflight_exit.pop(position.ticker, None)
        if prior:
            self.broker.cancel_order(prior)
        order = OrderRequest(
            ticker=position.ticker,
            action="sell",
            side="yes",
            price_cents=decision.limit_price_cents,
            count=decision.count,
            client_order_id=self._next_id("sell", position.ticker),
        )
        res = self.broker.create_order(order, market)
        if res.ok and res.filled_count < decision.count and res.order_id:
            self._inflight_exit[position.ticker] = res.order_id
        return res

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
        # We're closing/halting everything; drop any in-flight order tracking.
        self._inflight_entry.clear()
        self._inflight_exit.clear()
