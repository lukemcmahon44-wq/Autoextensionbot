# RISKS — read this before running live

This bot trades **real money** by default. Automated trading can lose money fast
and without supervision. Nothing here promises, targets, or implies any rate of
return. You are responsible for every order it places. Run paper first, start
small, and keep the caps conservative.

## The strategy's core risk: pennies in front of a steamroller

The default strategy buys YES contracts at 96–99¢ that look near-certain to
settle at 100¢. Most of the time it collects a few cents. The danger is the rare
case that settles at **0** — a single loss is ~20–25× a typical win. A long
streak of wins can be erased by one bad settlement. This payoff shape ("picking
up pennies in front of a steamroller") is exactly why position size is **hard
capped**: the cap, not the win rate, is what keeps a single blow-up survivable.

## The stop-loss cannot save you from a gap

The stop sells when the YES bid drops to `stop_loss_cents`. It only works if
there is a live bid to sell into. Near settlement, a binary market can jump
straight from ~98 to 0 — news drops, the event resolves — with **no tradeable
prints in between**. In that case the stop never fires and you lose the full
position cost. Treat the stop as a soft convenience for slow bleeds, **not** as
protection against settlement risk. The position-size cap is the real control.

## Other risks

- **Liquidity / slippage** — thin books mean your exit may fill worse than the
  marked bid, or not at all. Depth filters and slippage caps reduce but do not
  remove this.
- **Settlement / resolution** — markets can settle against you on information you
  don't have; "near-certain" is not certain.
- **Reconciliation lag** — the bot treats the exchange as the source of truth and
  re-syncs each cycle, but between syncs local state can briefly diverge (fills,
  partial fills, cancellations). It tracks resting orders in memory to avoid
  stacking duplicates; a crash can lose that tracking, but the per-position and
  total-exposure caps remain the hard backstop on how much can ever be at risk.
- **API / operational** — host or auth changes, rate limits, outages, clock skew,
  and bugs can all cause missed exits or stuck positions. The circuit breaker
  halts after repeated errors, but it is a backstop, not a guarantee.
- **Key security** — anyone with your RSA private key can trade your account.
  Keep it out of the repo (it is `.gitignore`d), `chmod 600`, never log it.
- **The paper simulator is not predictive** — it exists to exercise the code
  paths offline. Its fills and outcomes are synthetic and say **nothing** about
  live profitability.
- **Regulatory** — Kalshi is a regulated venue; trading availability and contract
  terms depend on your jurisdiction and account standing.

## What the guardrails do and don't do

The kill switch, daily loss limit, exposure/position/concurrency caps, pre-close
cutoff, and error breaker (see `src/risk.py`) bound **how much** can go wrong per
position, per day, and in aggregate. They do **not** make the strategy
profitable, and they cannot prevent a loss inside the caps you configured. If you
widen the caps, you widen the damage a bad day can do.

A few specifics worth understanding:

- **Kill switch vs. daily-loss limit are different.** The kill switch (`KILL`
  file) **freezes all automated activity** — no new entries *and* no automated
  exits — and a human takes over; it flattens only if `flatten_on_kill: true`
  (the default). The daily-loss limit only **halts new entries** while still
  managing exits (and flattens if `flatten_on_daily_limit: true`).
- **The error breaker self-clears.** Repeated errors halt new entries and alert,
  but protective stops keep running once the exchange is reachable again, and the
  breaker clears after one healthy cycle — it won't silently brick the bot.
- **Marking an illiquid/gapped position.** Equity marks a held position off its
  last seen YES bid. If a favorite gaps **straight** to no-bid, the latent loss
  isn't fully reflected in the daily-loss equity until the position **settles**
  (same day) — so on such a day the daily limit may engage at settlement rather
  than the instant of the gap. This is inherent: there is no tradeable price to
  mark against. It is, again, why the **position-size cap** is the real control.

## Stopping it fast

Drop a file named `KILL` in the working directory. Within one poll cycle the bot
halts all new entries and (if `kill_switch.flatten_on_kill` is true) flattens
every open position with marketable limit orders. Remove the file to resume. See
[`RUNBOOK.md`](RUNBOOK.md).
