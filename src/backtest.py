"""Offline backtest harness.

Runs the bot's REAL decision code -- the scanner filters, ``plan_entry`` sizing,
``should_exit`` stop/take-profit, and the ``RiskManager`` caps -- over many
simulated same-day sessions and reports the distribution of outcomes, including
the bad days.

The market model is deliberately HONEST: a contract priced at ``p`` cents settles
YES with probability ``p/100`` (an efficient market with no edge). So the result
isolates the mechanical cost of the strategy -- crossing the spread on entry and
locking in losses at the stop -- rather than flattering it with a made-up edge.

    python -m src.backtest --trials 300 --start-balance 1000

IMPORTANT: this is a synthetic model, not a prediction. Real Kalshi fees, real
liquidity, and real (non-efficient) market behavior will all differ. Use it to
understand the strategy's SHAPE of risk, not to forecast profit.
"""
from __future__ import annotations

import argparse
import copy
import logging
import os
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import List

from . import config as config_mod
from .main import App
from .timeutil import resolve_tz


class ManualClock:
    """A clock we step by hand so a session is fully deterministic."""

    def __init__(self, start: float):
        self._t = start

    def now(self) -> float:
        return self._t

    def set(self, t: float) -> None:
        self._t = t


@dataclass
class TrialResult:
    pnl: float
    pnl_pct: float
    max_drawdown_pct: float
    orders: int


def _equity(app: App) -> float:
    account = app.executor.broker.get_account()
    marks = {t: app.data_source.orderbook_top(t)[0] for t in account.positions}
    return account.equity_usd(marks)


def _day_start(settings) -> float:
    """A fixed 09:00 in the settlement timezone, so an 8h day stays same-day."""
    tz = resolve_tz(settings.strategy.get("settlement_timezone"))
    d = datetime(2024, 6, 3, 9, 0)
    if tz is not None:
        d = d.replace(tzinfo=tz)
    return d.timestamp()


def simulate_day(base_settings, *, seed: int, start_balance: float, steps: int = 40,
                 gap_prob: float = 0.15) -> TrialResult:
    s = copy.deepcopy(base_settings)
    s.paper = True
    s.api_base = s.raw["mode"]["api_base_demo"]
    s.raw["mode"].update(
        paper_data_source="sim",
        sim_seed=seed,
        sim_win_prob=None,            # efficient-market baseline (no edge)
        sim_gap_prob=gap_prob,        # tail risk: favorites that gap to 0 through the stop
        paper_start_balance=start_balance,
    )

    start = _day_start(s)
    clock = ManualClock(start)
    app = App(s, clock, persist=False)
    horizon = s.strategy["max_hours_to_close"] * 3600

    peak = start_balance
    max_dd = 0.0
    for i in range(1, steps + 1):
        clock.set(start + (i / steps) * horizon * 0.98)
        app.run_cycle()
        eq = _equity(app)
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    # Advance past every close so all open positions settle, then read final cash.
    clock.set(start + horizon + 7200)
    app.run_cycle()
    final = app.executor.broker.balance_usd

    pnl = final - start_balance
    return TrialResult(
        pnl=pnl,
        pnl_pct=100.0 * pnl / start_balance,
        max_drawdown_pct=100.0 * max_dd / start_balance,
        orders=app.executor._seq,
    )


def run_backtest(base_settings, *, trials: int, start_balance: float, steps: int = 40,
                 base_seed: int = 1000, gap_prob: float = 0.15) -> List[TrialResult]:
    return [
        simulate_day(base_settings, seed=base_seed + i, start_balance=start_balance,
                     steps=steps, gap_prob=gap_prob)
        for i in range(trials)
    ]


def summarize(results: List[TrialResult]) -> dict:
    pnls = sorted(r.pnl_pct for r in results)
    n = len(pnls)

    def pct(p: float) -> float:
        if n == 0:
            return 0.0
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return pnls[idx]

    return {
        "trials": n,
        "mean_pnl_pct": statistics.fmean(pnls) if n else 0.0,
        "median_pnl_pct": statistics.median(pnls) if n else 0.0,
        "p10_pnl_pct": pct(10),
        "p90_pnl_pct": pct(90),
        "worst_day_pct": pnls[0] if n else 0.0,
        "best_day_pct": pnls[-1] if n else 0.0,
        "pct_days_profitable": 100.0 * sum(1 for x in pnls if x > 0) / n if n else 0.0,
        "worst_drawdown_pct": max((r.max_drawdown_pct for r in results), default=0.0),
        "mean_orders": statistics.fmean([r.orders for r in results]) if n else 0.0,
    }


def _print_report(stats: dict, start_balance: float) -> None:
    print("\n================ BACKTEST (efficient-market, synthetic) ================")
    print(f"  trials (sim days):     {stats['trials']}")
    print(f"  start balance:         ${start_balance:,.2f}")
    print(f"  mean daily P&L:        {stats['mean_pnl_pct']:+.2f}%")
    print(f"  median daily P&L:      {stats['median_pnl_pct']:+.2f}%")
    print(f"  10th percentile day:   {stats['p10_pnl_pct']:+.2f}%")
    print(f"  90th percentile day:   {stats['p90_pnl_pct']:+.2f}%")
    print(f"  worst day:             {stats['worst_day_pct']:+.2f}%")
    print(f"  best day:              {stats['best_day_pct']:+.2f}%")
    print(f"  days profitable:       {stats['pct_days_profitable']:.1f}%")
    print(f"  worst intraday dd:     {stats['worst_drawdown_pct']:.2f}%")
    print(f"  avg orders/day:        {stats['mean_orders']:.1f}")
    print("========================================================================")
    print("NOTE: synthetic, no-edge, fee-free model. Real fees + real markets differ.")
    print("This shows the SHAPE of risk (fat-tailed downside), not a profit forecast.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="kalshi-scalper backtest")
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--start-balance", type=float, default=1000.0)
    parser.add_argument("--steps", type=int, default=40, help="decision cycles per simulated day")
    parser.add_argument("--markets", type=int, default=8, help="simulated markets per day")
    parser.add_argument("--gap-prob", type=float, default=0.15,
                        help="fraction of resolutions that GAP through the stop (tail risk)")
    parser.add_argument("--seed", type=int, default=1000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)  # quiet the per-cycle chatter
    # The backtest is always an offline dry-run; force paper so the live fail-closed
    # gate (config ships live) never blocks a pure analysis run.
    os.environ["FORCE_PAPER"] = "true"
    settings = config_mod.load_settings("config.yaml")
    settings.raw["mode"]["sim_markets"] = args.markets

    results = run_backtest(settings, trials=args.trials, start_balance=args.start_balance,
                           steps=args.steps, base_seed=args.seed, gap_prob=args.gap_prob)
    _print_report(summarize(results), args.start_balance)


if __name__ == "__main__":
    main()
