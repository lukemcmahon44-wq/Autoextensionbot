# RUNBOOK

Operational guide: run paper, go live, deploy, read alerts, stop fast.

## 1. Run paper (safe — no real-money orders)

```bash
pip install -r requirements.txt
cp .env.example .env                 # FORCE_PAPER=true is already set there
FORCE_PAPER=true python -m src.main
```

- `FORCE_PAPER=true` forces paper mode regardless of `config.yaml`. Orders are
  simulated locally; **no real-money endpoint is called.**
- Without reachable demo credentials it uses the built-in **offline simulator**,
  so it runs anywhere with no network.
- Watch the logs: every line is tagged `[PAPER]` or `[LIVE]`, and every decision
  (scan result, entry, exit, halt) is logged.

Quick compressed dry-run (a few cycles, accelerated simulator):

```bash
FORCE_PAPER=true MAX_CYCLES=30 POLL_INTERVAL_SEC=0.2 SIM_TIME_SCALE=10000 python -m src.main
```

## 2. Go live — the exact change

Live trading needs two things: **real credentials** and **paper turned off**.

1. Put real values in `.env` and **remove/disable `FORCE_PAPER`**:
   ```dotenv
   KALSHI_API_KEY_ID=<your key id>
   KALSHI_PRIVATE_KEY_PATH=/secure/path/kalshi_private_key.pem
   ALERT_WEBHOOK_URL=<your webhook>     # optional but recommended
   # FORCE_PAPER=                       # <-- the one-line change: leave it UNSET/empty
   ```
2. `config.yaml` already ships live (`mode.paper_trading: false`). Leave it.
3. Verify the base URLs in `config.yaml` (`mode.api_base_live`) against
   <https://docs.kalshi.com> — Kalshi has moved hosts before.
4. Start it:
   ```bash
   python -m src.main
   ```

The bot **fails closed**: if the key id or private key is missing or unreadable,
a live run prints `REFUSING TO START: ...` and exits with code 2 — it will not
trade. Re-run a final paper check before going live.

> Sanity check you're live: the logs say `LIVE mode: real-money trading against
> <host>` and alerts are tagged `[LIVE]`.

## 2b. Set your boundaries (do this before live)

All limits live in `config.yaml`. The shipped defaults are deliberately small —
**lower them to your bankroll; do not raise them to trade more.**

| Setting | Default | What it bounds |
|---|---|---|
| `risk.max_position_usd` | 50 | Most you can have in one market |
| `risk.max_total_exposure_usd` | 250 | Most you can have at risk across all markets |
| `risk.max_concurrent_positions` | 3 | How many markets at once |
| `risk.daily_loss_limit_pct` | 5 | Day's loss before it halts (and flattens) |
| `risk.no_new_entries_before_close_min` | 10 | Quiet window before close (gap risk) |
| `strategy.entry_min_cents` / `entry_max_cents` | 96 / 99 | The scalp band |
| `strategy.stop_loss_cents` | 93 | Auto-sell trigger |
| `strategy.take_profit_cents` | null | null = hold winners to settlement |
| `strategy.max_hours_to_close` | 8 | Only markets closing this soon, same Eastern day |

**Optional (advanced) — stronger exchange-level guarantees.** By default the bot
sends plain resting limit orders and prevents order-stacking itself. If, during a
demo run, you confirm the exact field values against <https://docs.kalshi.com>,
you can set them in `config.yaml -> order:` for belt-and-suspenders safety:

```yaml
order:
  time_in_force: <the immediate/fill-or-kill value>  # entries fill now or cancel, never rest
  reduce_only: true                                   # a sell can only ever reduce a position
```

If you set a wrong value the exchange rejects the order (no bad trade — it just
won't trade until you fix it), which is why these are off by default.

## 3. Deploy (always-on)

**Docker:**
```bash
docker build -t kalshi-scalper .
docker run -d --name kalshi-scalper --restart unless-stopped \
  --env-file .env \
  -v $PWD/kalshi_private_key.pem:/run/secrets/kalshi.pem:ro \
  -e KALSHI_PRIVATE_KEY_PATH=/run/secrets/kalshi.pem \
  kalshi-scalper
docker logs -f kalshi-scalper
```

**systemd:** see header comments in [`deploy/kalshi-scalper.service`](deploy/kalshi-scalper.service).
```bash
sudo systemctl enable --now kalshi-scalper
journalctl -u kalshi-scalper -f
```

State persists to `state.json`, so a restart resumes the daily P&L baseline,
error counter, and (in paper) the simulated book cleanly.

## 4. Reading alerts

If `ALERT_WEBHOOK_URL` is set, the bot posts to it (Slack/Discord/generic) on:

- **TRADE** — every entry and exit: `TRADE BUY/SELL <ticker> x<count> @ <price>c`.
- **HALT / KILL** — kill switch, daily-loss limit, or error breaker tripping;
  notes if it flattened.
- **ERROR** — a cycle threw; the loop keeps running and counts it toward the
  error breaker.
- **DAILY SUMMARY** — at the day rollover: equity, day P&L %, open positions.

Alerts are also written to the logs. Webhook failures are swallowed — they never
crash the loop.

## 5. Stop it fast

| You want to… | Do this |
|---|---|
| Halt + flatten now | `touch KILL` (in the working dir). Within one poll cycle it halts entries and flattens (if `flatten_on_kill: true`). |
| Halt entries but keep positions | set `kill_switch.flatten_on_kill: false`, then `touch KILL` |
| Resume | `rm KILL` |
| Stop the process | `docker stop kalshi-scalper` / `sudo systemctl stop kalshi-scalper` / Ctrl-C (it stops after the current cycle) |

`KILL` is `.gitignore`d — it lives only on the running host.

## 6. Troubleshooting

- **`REFUSING TO START: LIVE mode requires KALSHI_API_KEY_ID`** — you're in live
  mode without creds. Set them, or `FORCE_PAPER=true` to dry-run.
- **`could not parse RSA private key`** — wrong file or format; point
  `KALSHI_PRIVATE_KEY_PATH` at the PEM you downloaded from Kalshi.
- **Repeated `ERROR` alerts then `HALT: error circuit breaker`** — the exchange or
  network is failing; the bot halted itself. Investigate, then restart (or it
  clears after a clean cycle). Check base URLs and key validity first.
- **No entries** — normal when nothing is in-band/liquid/same-day, or you're
  inside the pre-close cutoff, or a halt is active. The logs say which.
