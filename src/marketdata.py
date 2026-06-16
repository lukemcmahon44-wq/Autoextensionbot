"""Market-data sources.

Two implementations behind one interface:

* ``LiveMarketData``  -- reads real markets/orderbooks from Kalshi (live host in
  live mode, demo host in paper mode).
* ``SimMarketData``   -- a fully-offline simulator so ``FORCE_PAPER=true`` can run
  end-to-end with no network at all. It walks a handful of synthetic same-day
  markets toward a hidden binary outcome so a session exercises entries,
  stop-losses, take-profits and settlement.

Orderbook convention (Kalshi): the book has a ``yes`` array of resting YES bids
and a ``no`` array of resting NO bids, each ``[price_cents, quantity]``. The best
YES ask is therefore ``100 - best_no_bid``.
"""
from __future__ import annotations

import logging
import random
from typing import Dict, List, Optional, Protocol, Tuple

from .models import Market

log = logging.getLogger("kalshi.marketdata")

YesTop = Tuple[Optional[int], Optional[int], int, int]  # bid, ask, bid_depth, ask_depth


def parse_yes_top(orderbook: Dict) -> YesTop:
    """Collapse a raw Kalshi orderbook into YES top-of-book + depths."""
    yes = orderbook.get("yes") or []   # resting YES bids
    no = orderbook.get("no") or []     # resting NO bids

    yes_bid = max((lvl[0] for lvl in yes), default=None)
    yes_bid_depth = (
        sum(lvl[1] for lvl in yes if lvl[0] == yes_bid) if yes_bid is not None else 0
    )

    best_no = max((lvl[0] for lvl in no), default=None)
    yes_ask = (100 - best_no) if best_no is not None else None
    yes_ask_depth = (
        sum(lvl[1] for lvl in no if lvl[0] == best_no) if best_no is not None else 0
    )
    return yes_bid, yes_ask, int(yes_bid_depth), int(yes_ask_depth)


class MarketDataSource(Protocol):
    def list_candidate_markets(self, now: float, max_close_ts: int) -> List[Market]: ...
    def orderbook_top(self, ticker: str) -> YesTop: ...
    def settlement_value_cents(self, ticker: str, now: float) -> Optional[int]: ...


# --------------------------------------------------------------------------
class LiveMarketData:
    """Reads from a real Kalshi host via :class:`KalshiClient`."""

    def __init__(self, client):
        self.client = client

    def list_candidate_markets(self, now: float, max_close_ts: int) -> List[Market]:
        raw = self.client.list_all_markets(
            status="open", min_close_ts=int(now), max_close_ts=int(max_close_ts)
        )
        out: List[Market] = []
        for m in raw:
            out.append(
                Market(
                    ticker=m["ticker"],
                    status=m.get("status", "open"),
                    close_ts=int(m.get("close_ts") or m.get("close_time_ts") or 0),
                    yes_bid=m.get("yes_bid"),
                    yes_ask=m.get("yes_ask"),
                    yes_ask_depth=0,  # filled in by orderbook_top during fine filtering
                    yes_bid_depth=0,
                    title=m.get("title", ""),
                )
            )
        return out

    def orderbook_top(self, ticker: str) -> YesTop:
        return parse_yes_top(self.client.get_orderbook(ticker))

    def settlement_value_cents(self, ticker: str, now: float) -> Optional[int]:
        # Live settlement is the exchange's job; reconciliation reflects it in
        # balance/positions. Nothing to simulate here.
        return None


# --------------------------------------------------------------------------
class _SimMarket:
    def __init__(self, ticker: str, close_ts: int, outcome_yes: bool, rng: random.Random):
        self.ticker = ticker
        self.close_ts = close_ts
        self.outcome_yes = outcome_yes      # hidden terminal truth (YES->100, NO->0)
        self.rng = rng
        # Start most markets up in the "near-certain YES" band so the strategy
        # has things to bite on; a couple start lower / destined NO to exercise
        # the stop-loss path.
        self.mid = rng.choice([90, 94, 96, 97, 98])
        # Gap risk: a "gap" market holds near its entry price and then jumps
        # DISCONTINUOUSLY to its terminal value -- blowing straight through the
        # stop. This is the real tail risk of buying near-certain favorites, so a
        # smooth-walk-only sim would dangerously understate the downside.
        self.gap = False
        self.gap_ts = close_ts

    def mid_at(self, now: float, open_ts: float) -> int:
        target = 100 if self.outcome_yes else 0
        if self.gap:
            if now >= self.gap_ts:
                # The jump: stop never gets a tradeable price on the way down.
                return min(99, max(1, target))
            # Hover near the entry band until the jump.
            return int(min(99, max(1, round(self.mid + self.rng.uniform(-1.0, 1.0)))))
        # Non-gap markets converge smoothly toward the terminal value.
        span = max(1.0, self.close_ts - open_ts)
        frac = min(1.0, max(0.0, (now - open_ts) / span))
        mid = self.mid + (target - self.mid) * (frac ** 2)
        mid += self.rng.uniform(-1.5, 1.5)
        return int(min(99, max(1, round(mid))))


class SimMarketData:
    """Offline market simulator for paper runs with no network access."""

    def __init__(self, now: float, max_hours_to_close: float, *, seed: int = 7,
                 n: int = 6, win_prob: Optional[float] = 0.6, gap_prob: float = 0.0):
        self.rng = random.Random(seed)
        self.open_ts = now
        horizon = max_hours_to_close * 3600
        self.markets: Dict[str, _SimMarket] = {}
        for i in range(n):
            close = int(now + self.rng.uniform(0.4, 0.95) * horizon)
            tk = f"SIM-EVENT-{i:02d}"
            if win_prob is not None:
                # Fixed win rate (used by the demo runs).
                outcome = self.rng.random() < win_prob
                m = _SimMarket(tk, close, outcome, self.rng)
            else:
                # Efficient-market baseline (used by the backtest): a market priced
                # at p cents settles YES with probability p/100 -- i.e. NO edge, so
                # the result shows the honest cost of crossing the spread + stops.
                m = _SimMarket(tk, close, False, self.rng)
                m.outcome_yes = self.rng.random() < (m.mid / 100.0)
            # `gap_prob and ...` short-circuits so the default (0.0) consumes no
            # rng draw -- keeps the demo runs/tests byte-for-byte deterministic.
            if gap_prob and self.rng.random() < gap_prob:
                m.gap = True
                m.gap_ts = int(now + self.rng.uniform(0.6, 0.9) * horizon)
            self.markets[tk] = m
        log.info("SimMarketData: %d synthetic same-day markets generated", n)

    def list_candidate_markets(self, now: float, max_close_ts: int) -> List[Market]:
        out: List[Market] = []
        for m in self.markets.values():
            if now >= m.close_ts:
                continue  # already closed/settled
            if m.close_ts > max_close_ts:
                continue
            mid = m.mid_at(now, self.open_ts)
            bid = max(1, mid - 1)
            ask = min(99, mid + 1)
            out.append(
                Market(
                    ticker=m.ticker,
                    status="open",
                    close_ts=m.close_ts,
                    yes_bid=bid,
                    yes_ask=ask,
                    yes_bid_depth=200,
                    yes_ask_depth=200,
                    title=f"Simulated event {m.ticker}",
                )
            )
        return out

    def orderbook_top(self, ticker: str) -> YesTop:
        m = self.markets.get(ticker)
        if m is None:
            return (None, None, 0, 0)
        mid = m.mid_at(_clock(), self.open_ts)
        return (max(1, mid - 1), min(99, mid + 1), 200, 200)

    def settlement_value_cents(self, ticker: str, now: float) -> Optional[int]:
        m = self.markets.get(ticker)
        if m is None or now < m.close_ts:
            return None
        return 100 if m.outcome_yes else 0


# A module-level clock indirection so SimMarketData.orderbook_top can mark to the
# same "now" the loop is using even when called without an explicit timestamp.
_CLOCK = [None]  # type: List


def set_sim_clock(now_fn) -> None:
    _CLOCK[0] = now_fn


def _clock() -> float:
    import time

    return _CLOCK[0]() if _CLOCK[0] else time.time()
