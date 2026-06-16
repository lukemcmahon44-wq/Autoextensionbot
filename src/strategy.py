"""Entry sizing and exit decisions. Pure functions of (market, account, config).

Honesty about the stop-loss: this is a "buy near-certain YES" strategy. The
stop at ``stop_loss_cents`` is a *soft* protection. It can only fire when there is
a live bid to sell into, and near settlement a binary market can gap straight
from ~98 to 0 with no tradeable prints in between -- the stop will NOT save you
there. That residual tail is exactly why position size is hard-capped: the cap,
not the stop, is the real risk control.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Mapping, Optional

from .models import AccountState, EntryPlan, Market, Position, cents_to_usd

log = logging.getLogger("kalshi.strategy")


@dataclass
class ExitDecision:
    reason: str            # "stop_loss" | "take_profit"
    limit_price_cents: int
    count: int


def plan_entry(
    market: Market,
    account: AccountState,
    strategy_cfg: Mapping,
    *,
    max_position_usd: float,
    remaining_exposure_usd: float,
) -> Optional[EntryPlan]:
    """Build a buy-limit plan, sized to the tightest of the budget constraints.

    Returns None if nothing can be bought (no room, no depth, or out of band).
    The plan is still subject to ``RiskManager.check_entry`` -- this only sizes.
    """
    if market.yes_ask is None:
        return None

    entry_max = strategy_cfg["entry_max_cents"]
    limit_price = min(market.yes_ask + strategy_cfg["max_entry_slippage_cents"], entry_max)
    limit_price = min(99, max(1, limit_price))
    if not (strategy_cfg["entry_min_cents"] <= limit_price <= entry_max):
        return None

    existing = account.positions.get(market.ticker)
    existing_cost = existing.cost_usd if existing else 0.0

    # Budget = the smallest of: per-position room, remaining exposure budget, cash.
    per_position_room = max_position_usd - existing_cost
    budget = min(per_position_room, remaining_exposure_usd, account.balance_usd)
    if budget <= 0:
        return None

    price_usd = cents_to_usd(limit_price)
    count = int(math.floor(budget / price_usd))

    # Never try to lift more than the book is offering at the ask.
    if market.yes_ask_depth > 0:
        count = min(count, market.yes_ask_depth)

    if count <= 0:
        return None

    return EntryPlan(
        ticker=market.ticker,
        limit_price_cents=limit_price,
        count=count,
        title=market.title,
    )


def should_exit(
    position: Position,
    market: Market,
    strategy_cfg: Mapping,
) -> Optional[ExitDecision]:
    """Decide whether to sell a position to close, marking off the live YES bid."""
    yes_bid = market.yes_bid
    if yes_bid is None:
        # No bid to hit -- we cannot place a sell that would fill. Hold and let the
        # next cycle (or settlement) handle it. Logged loudly because a stop we
        # *want* to take but can't is exactly the dangerous gap case.
        log.warning("%s: no live YES bid; cannot exit this cycle", position.ticker)
        return None

    slippage = strategy_cfg["max_exit_slippage_cents"]
    stop = strategy_cfg["stop_loss_cents"]
    take = strategy_cfg.get("take_profit_cents")

    # A marketable sell limit: allowed to sit up to ``slippage`` cents under the
    # bid to make sure it crosses and fills.
    sell_limit = max(1, yes_bid - slippage)

    if yes_bid <= stop:
        return ExitDecision("stop_loss", sell_limit, position.count)

    if take is not None and yes_bid >= take:
        return ExitDecision("take_profit", sell_limit, position.count)

    return None
