# kalshi-scalper

An autonomous, **guardrail-first** trading bot for [Kalshi](https://kalshi.com)
event contracts. It scans same-day markets for near-certain YES contracts, buys
them with limit orders, manages them with a stop-loss and optional take-profit,
and runs hands-off — with a kill switch, hard risk caps, and a circuit breaker
wired in front of every order.

> ⚠️ **This bot trades real money.** `config.yaml` ships with
> `mode.paper_trading: false` — that is deliberate, the owner runs it live with
> their own keys. **While developing or testing, always set `FORCE_PAPER=true`**
> so your runs are simulated and never touch a real-money endpoint. The bot also
> **fails closed**: a live run refuses to start without valid API credentials.
>
> Read [`RISKS.md`](RISKS.md) before running it live. No part of this project
> promises or targets any rate of return.

## Strategy (all knobs live in `config.yaml`)

- **Universe** — open Kalshi markets whose close time is between now and
  `strategy.max_hours_to_close`, **same calendar day only** (no overnight risk).
- **Entry** — when the YES ask is in `[entry_min_cents, entry_max_cents]`
  (default 96–99), the spread is `<= max_entry_spread_cents`, and best-ask depth
  is `>= min_orderbook_depth_contracts`, place a **YES buy limit** at
  `min(ask + max_entry_slippage_cents, entry_max_cents)`, sized to the smaller of
  `max_position_usd` and the remaining exposure budget (and never larger than the
  book on offer).
- **Exit** — mark each position off the **live YES bid**:
  - **Stop-loss**: bid `<= stop_loss_cents` (default 93) → sell to close now, with
    a limit bounded by `max_exit_slippage_cents`.
  - **Take-profit**: if `take_profit_cents` is set and bid `>= take_profit_cents`
    → sell to close. If `null`, winners are held to settlement.

**Honest note on the stop:** this is a "buy near-certain YES" strategy. The stop
is *soft* — it can only fire when there is a live bid to sell into, and a binary
market near settlement can gap straight from ~98 to 0 with nothing tradeable in
between. The stop will not save you there. The real risk control is the **hard
position-size cap**, not the stop. See [`RISKS.md`](RISKS.md).

## Safety layer (`src/risk.py` — never bypassed)

Every entry passes `RiskManager.check_entry(...)`; every loop calls
`update_daily_pnl(...)`. Enforced:

| Guardrail | Config | Effect |
|---|---|---|
| Kill switch file | `kill_switch.file` (`KILL`) | Instant global halt; flattens if `flatten_on_kill` |
| Daily loss limit | `risk.daily_loss_limit_pct` | Halts new entries for the day; flattens if `flatten_on_daily_limit` |
| Per-position cap | `risk.max_position_usd` | Caps cost basis of any one position |
| Total exposure cap | `risk.max_total_exposure_usd` | Caps summed cost basis |
| Concurrency cap | `risk.max_concurrent_positions` | Caps number of open markets |
| Pre-close cutoff | `risk.no_new_entries_before_close_min` | No entries in the final minutes |
| Error breaker | `risk.max_consecutive_errors` | Halts + alerts after repeated failures |
| Same-day only | `strategy.settlement_timezone` | "Today" is the exchange (US/Eastern) day, so a UTC server can't pick up a market that settles the next trading day |
| No order stacking | `loop.entry_order_ttl_cycles` | An entry that doesn't fill is tracked, never re-stacked, and cancelled after the TTL; resting stop-sells are cancelled before repricing so a bounce can't oversell |

**Default boundaries (conservative — lower them to your bankroll; don't raise to "trade more"):**
`max_position_usd 50`, `max_total_exposure_usd 250`, `max_concurrent_positions 3`,
`daily_loss_limit_pct 5%` (flattens on hit), entry band `96–99`, stop `93`,
`take_profit null` (hold winners to settlement), `max_hours_to_close 8` (same Eastern day).

## Quick start (safe paper dry-run)

```bash
pip install -r requirements.txt
cp .env.example .env            # FORCE_PAPER=true is already set in the example
FORCE_PAPER=true python -m src.main
```

With `FORCE_PAPER=true` the bot runs in paper mode: it reads the demo API when
reachable (and you have demo credentials), otherwise it falls back to a fully
**offline market simulator** so it runs end-to-end with no network at all. Orders
are simulated locally — no real-money endpoint is ever called. Logs make the
active mode obvious (`[PAPER]` vs `[LIVE]`).

Handy dev/test env overrides: `MAX_CYCLES` (run N cycles then exit),
`POLL_INTERVAL_SEC`, `SIM_TIME_SCALE` (paper only — accelerate the simulator to
compress a session), `PAPER_START_BALANCE`.

## Going live

See [`RUNBOOK.md`](RUNBOOK.md) for the exact one-line change, credential setup,
and deployment. In short: provide real credentials in `.env`, unset
`FORCE_PAPER`, and keep `mode.paper_trading: false`.

## Tests

```bash
pytest -q
```

Covers the risk guardrails, scanner filters, sizing, exit logic, idempotent
orders, paper fills, settlement, reconciliation, RSA-PSS signing, and the wired
loop (including kill-switch flatten and daily-limit halt).

## Architecture

```
src/
  config.py        load config.yaml + env; FORCE_PAPER; fail-closed credential gate
  models.py        typed dataclasses (cents vs USD kept explicit)
  risk.py          the safety layer (never bypassed)
  kalshi_client.py Kalshi trade-api v2 client: manual RSA-PSS signing, retry/backoff
  marketdata.py    live data source + offline simulator; orderbook parsing
  scanner.py       same-day / in-band / spread / depth universe filter
  strategy.py      plan_entry sizing + should_exit (stop / take-profit)
  executor.py      idempotent orders, paper + live brokers, flatten, reconcile
  notifier.py      webhook alerts that never crash the loop
  state.py         atomic state.json persistence (crash-safe resume)
  main.py          the crash-resistant loop
deploy/            systemd unit
```

## API notes

Kalshi has changed API hosts more than once, so **base URLs are config-driven**
(`mode.api_base_live` / `mode.api_base_demo`), not hardcoded — verify them
against <https://docs.kalshi.com> before going live. Auth is RSA-PSS/SHA-256 over
`timestamp + METHOD + path` via the `KALSHI-ACCESS-*` headers; the key is loaded
from a file referenced by env and is **never logged**.
