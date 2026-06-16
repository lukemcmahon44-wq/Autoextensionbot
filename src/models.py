"""Plain data structures shared across the bot.

Prices are integer CENTS (1..99) the way Kalshi quotes them. USD amounts are
floats of dollars. Keeping the two unit systems explicit in names (``_cents`` vs
``_usd``) avoids the classic off-by-100 bug.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


def cents_to_usd(cents: int) -> float:
    """A single contract priced at ``cents`` costs this many dollars."""
    return cents / 100.0


@dataclass
class Market:
    """A snapshot of one Kalshi market, already merged with top-of-book."""

    ticker: str
    status: str                # "open", "closed", "settled", ...
    close_ts: int              # unix seconds, when the market closes/settles
    yes_bid: Optional[int]     # best YES bid in cents (None if no bids)
    yes_ask: Optional[int]     # best YES ask in cents (None if no asks)
    yes_ask_depth: int = 0     # contracts available at/inside best ask
    yes_bid_depth: int = 0     # contracts wanted at/inside best bid
    title: str = ""

    @property
    def spread_cents(self) -> Optional[int]:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid


@dataclass
class Position:
    """A held position. Kalshi reports YES exposure as a positive contract count."""

    ticker: str
    count: int                 # number of YES contracts held (>0)
    avg_price_cents: int       # average entry price in cents

    @property
    def cost_usd(self) -> float:
        return self.count * cents_to_usd(self.avg_price_cents)

    def mark_usd(self, yes_bid_cents: Optional[int]) -> float:
        """Mark-to-market value using the live YES bid (conservative)."""
        if yes_bid_cents is None:
            # No bid to sell into -- value at cost to avoid inventing equity.
            return self.cost_usd
        return self.count * cents_to_usd(yes_bid_cents)


@dataclass
class AccountState:
    """The reconciled truth pulled from Kalshi (or simulated in paper mode)."""

    balance_usd: float
    positions: Dict[str, Position] = field(default_factory=dict)

    def total_exposure_usd(self) -> float:
        return sum(p.cost_usd for p in self.positions.values())

    def equity_usd(self, marks: Dict[str, Optional[int]]) -> float:
        """Cash + marked value of every open position."""
        mtm = sum(p.mark_usd(marks.get(t)) for t, p in self.positions.items())
        return self.balance_usd + mtm


@dataclass
class EntryPlan:
    """A concrete buy intent produced by the strategy and vetted by risk."""

    ticker: str
    limit_price_cents: int     # YES buy limit price
    count: int                 # contracts to buy
    title: str = ""

    @property
    def cost_usd(self) -> float:
        return self.count * cents_to_usd(self.limit_price_cents)


@dataclass
class OrderRequest:
    """Everything needed to place one limit order. Market orders are forbidden."""

    ticker: str
    action: str                # "buy" | "sell"
    side: str                  # "yes" | "no" (this bot trades YES)
    price_cents: int           # limit price in cents
    count: int
    client_order_id: str       # idempotency key -- a retry can never double-fill

    def __post_init__(self) -> None:
        if self.action not in ("buy", "sell"):
            raise ValueError(f"bad action {self.action!r}")
        if self.side not in ("yes", "no"):
            raise ValueError(f"bad side {self.side!r}")
        if not 1 <= self.price_cents <= 99:
            raise ValueError(f"price {self.price_cents} out of 1..99")
        if self.count <= 0:
            raise ValueError("count must be positive")
        if not self.client_order_id:
            raise ValueError("client_order_id is required (idempotency)")


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str] = None
    filled_count: int = 0
    avg_fill_cents: Optional[int] = None
    error: Optional[str] = None
