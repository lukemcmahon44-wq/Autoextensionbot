"""Demo-environment smoke test.

Validates that auth, the API host, and every endpoint this bot relies on actually
work against YOUR Kalshi DEMO key -- WITHOUT placing any order by default. Run it
before going live to catch host/endpoint/credential problems early:

    FORCE_PAPER=true python -m src.smoke

To also validate the order create+cancel round-trip (DEMO host only), add a flag.
The test order is 1 contract priced at 1c (far from any market, so it cannot
fill) and is cancelled immediately. It refuses to run against a non-demo host:

    FORCE_PAPER=true python -m src.smoke --place-test-order

Exit code 0 = all checks passed, 1 = a check failed, 2 = misconfigured.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from typing import List, Tuple

from . import config as config_mod
from .kalshi_client import KalshiClient
from .marketdata import parse_yes_top
from .models import OrderRequest

log = logging.getLogger("kalshi.smoke")


def _check(name: str, fn, results: List[Tuple[str, bool, str]]):
    try:
        detail = fn() or ""
        log.info("PASS  %-24s %s", name, detail)
        results.append((name, True, detail))
        return detail
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the whole run
        log.error("FAIL  %-24s %s", name, exc)
        results.append((name, False, str(exc)))
        return None


def _order_roundtrip(client, settings, markets) -> str:
    # Hard guard: never place an order against a non-demo (live) host here.
    if "demo" not in settings.api_base.lower():
        raise RuntimeError(f"refusing to place a test order against non-demo host {settings.api_base}")
    if not markets:
        raise RuntimeError("no market available to test against")
    ticker = markets[0]["ticker"]
    order = OrderRequest(
        ticker=ticker, action="buy", side="yes", price_cents=1, count=1,
        client_order_id=f"ks-smoke-{int(time.time())}",
    )
    resp = client.create_order(order)
    order_id = str(resp.get("order_id") or resp.get("id") or "")
    if not order_id:
        raise RuntimeError(f"order accepted but no id returned: {resp}")
    client.cancel_order(order_id)
    return f"placed+cancelled 1@1c on {ticker} (order_id={order_id})"


def run_smoke(client, settings, *, place_test_order: bool = False) -> bool:
    """Run the read-only checks (plus optional order round-trip). Returns all-passed."""
    results: List[Tuple[str, bool, str]] = []
    strat = settings.strategy
    now = time.time()
    window_end = int(now + strat["max_hours_to_close"] * 3600)

    def _status():
        st = client.get_exchange_status()
        return f"reachable + authenticated ({st.get('exchange_active', 'ok')})"

    _check("auth / exchange status", _status, results)
    _check("balance", lambda: f"${client.get_balance_usd():.2f} available", results)

    holder = {}

    def _markets():
        ms = client.list_all_markets(status="open", min_close_ts=int(now), max_close_ts=window_end)
        holder["markets"] = ms
        return f"{len(ms)} open markets closing within {strat['max_hours_to_close']}h"

    _check("list markets (window)", _markets, results)

    markets = holder.get("markets") or []
    if markets:
        ticker = markets[0]["ticker"]
        _check("orderbook + parse", lambda: _fmt_book(client.get_orderbook(ticker), ticker), results)
    else:
        log.warning("no open markets in window; skipping orderbook check (not a failure)")

    _check("positions", lambda: f"{len(client.get_positions_raw())} open position(s)", results)

    if place_test_order:
        _check("order create+cancel (demo)", lambda: _order_roundtrip(client, settings, markets), results)

    passed = sum(1 for _, ok, _ in results if ok)
    log.info("smoke: %d/%d checks passed", passed, len(results))
    return all(ok for _, ok, _ in results)


def _fmt_book(orderbook, ticker: str) -> str:
    bid, ask, _bd, ad = parse_yes_top(orderbook)
    return f"{ticker} yes_bid={bid} yes_ask={ask} ask_depth={ad}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi demo smoke test")
    parser.add_argument("--place-test-order", action="store_true",
                        help="also place+cancel a 1c test order (DEMO host only)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = config_mod.load_settings(os.environ.get("CONFIG_PATH", "config.yaml"))
    except config_mod.ConfigError as exc:
        print(f"config error: {exc}", flush=True)
        raise SystemExit(2)

    if not settings.paper:
        print("Run the smoke test in paper/demo mode: set FORCE_PAPER=true.", flush=True)
        raise SystemExit(2)
    if not (settings.secrets.api_key_id and settings.secrets.private_key_path):
        print("Smoke test needs your DEMO credentials: KALSHI_API_KEY_ID + "
              "KALSHI_PRIVATE_KEY_PATH in .env.", flush=True)
        raise SystemExit(2)

    log.info("smoke test against %s (place_test_order=%s)", settings.api_base, args.place_test_order)
    client = KalshiClient(settings.api_base, settings.secrets.api_key_id, settings.secrets.private_key_path)
    ok = run_smoke(client, settings, place_test_order=args.place_test_order)
    print("\nSMOKE TEST: " + ("PASSED ✅" if ok else "FAILED ❌"), flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
