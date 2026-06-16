"""Universe selection.

Filters the open Kalshi universe down to same-day, in-band, liquid YES markets.
Two passes: a coarse pass on the cheap market summary fields, then a fine pass on
the authoritative orderbook top (which also gives us best-ask depth).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, List, Mapping

from .marketdata import MarketDataSource
from .models import Market

log = logging.getLogger("kalshi.scanner")


def same_calendar_day(now: float, close_ts: float) -> bool:
    """True if ``close_ts`` falls on the same local calendar day as ``now``.

    The owner does not want anything settling later than today. We use the host's
    local date; operators in a different timezone from the exchange should keep
    ``max_hours_to_close`` conservative so this never straddles a day boundary.
    """
    return datetime.fromtimestamp(now).date() == datetime.fromtimestamp(close_ts).date()


class Scanner:
    def __init__(self, strategy_cfg: Mapping, *, now_fn: Callable[[], float] = time.time):
        self.cfg = strategy_cfg
        self._now = now_fn

    def _passes_band_and_spread(self, m: Market) -> bool:
        if m.yes_ask is None or m.yes_bid is None:
            return False
        if not (self.cfg["entry_min_cents"] <= m.yes_ask <= self.cfg["entry_max_cents"]):
            return False
        spread = m.spread_cents
        if spread is None or spread > self.cfg["max_entry_spread_cents"]:
            return False
        return True

    def scan(self, ds: MarketDataSource) -> List[Market]:
        now = self._now()
        window_end = int(now + self.cfg["max_hours_to_close"] * 3600)
        candidates = ds.list_candidate_markets(now, window_end)
        log.info("scanner: %d open markets in time window", len(candidates))

        # --- coarse pass: time + same-day + band/spread on summary fields ----
        coarse: List[Market] = []
        for m in candidates:
            if m.status != "open":
                log.debug("skip %s: status=%s", m.ticker, m.status)
                continue
            if m.close_ts <= now:
                log.debug("skip %s: already closed", m.ticker)
                continue
            if m.close_ts > window_end:
                log.debug("skip %s: closes beyond max_hours_to_close", m.ticker)
                continue
            if not same_calendar_day(now, m.close_ts):
                log.debug("skip %s: settles later than today", m.ticker)
                continue
            if not self._passes_band_and_spread(m):
                log.debug("skip %s: ask=%s bid=%s out of band/spread", m.ticker, m.yes_ask, m.yes_bid)
                continue
            coarse.append(m)

        # --- fine pass: authoritative top-of-book + depth --------------------
        passing: List[Market] = []
        for m in coarse:
            yes_bid, yes_ask, bid_depth, ask_depth = ds.orderbook_top(m.ticker)
            m.yes_bid, m.yes_ask = yes_bid, yes_ask
            m.yes_bid_depth, m.yes_ask_depth = bid_depth, ask_depth
            if not self._passes_band_and_spread(m):
                log.debug("skip %s: orderbook moved out of band/spread", m.ticker)
                continue
            if m.yes_ask_depth < self.cfg["min_orderbook_depth_contracts"]:
                log.debug(
                    "skip %s: ask depth %d < min %d",
                    m.ticker, m.yes_ask_depth, self.cfg["min_orderbook_depth_contracts"],
                )
                continue
            passing.append(m)

        # Closest-to-settlement first (highest ask), then deepest book.
        passing.sort(key=lambda x: (x.yes_ask or 0, x.yes_ask_depth), reverse=True)
        log.info("scanner: %d markets pass all filters", len(passing))
        return passing
