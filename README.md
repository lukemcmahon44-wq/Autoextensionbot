# Kalshi Trading Bot

A production-ready Python trading bot for [Kalshi](https://kalshi.com) prediction markets.

## What It Does

- Scans all open markets every 30 seconds
- **Buys** 1 YES contract on any market where the best YES ask is ≥ 97¢
- **Sells** immediately when the best YES ask drops below 65¢ (limit sell at best bid)
- Never holds more than 10 open positions
- Filters out illiquid markets (< 50 volume) and markets expiring in < 1 hour
- Logs every trade to SQLite and sends Telegram alerts on every entry/exit
- Handles rate limits with exponential backoff; restarts automatically via supervisord

---

## Project Structure

```
kalshi-bot/
├── main.py           # Entry point — starts the bot loop
├── bot.py            # Core trading logic (scanner, orders, exit watcher)
├── kalshi_client.py  # All Kalshi REST API v2 calls
├── db.py             # SQLite trade logging
├── alerts.py         # Telegram alert logic
├── pnl_report.py     # Standalone P&L report script
├── requirements.txt  # Pinned dependencies
├── .env.example      # Env var template
├── supervisord.conf  # Process supervisor config
├── railway.toml      # Railway.app deployment config
└── README.md
```

---

## Step 1 — Clone & Install

```bash
git clone <your-repo-url>
cd kalshi-bot

pip install -r requirements.txt
```

---

## Step 2 — Configure Environment

Copy the template and fill it in:

```bash
cp .env.example .env
```

Open `.env` in any text editor and set:

| Variable | Where to get it |
|---|---|
| `KALSHI_API_KEY` | [kalshi.com](https://kalshi.com) → Settings → API → Create Key |
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| `TELEGRAM_CHAT_ID` | Message [@userinfobot](https://t.me/userinfobot) — it replies with your chat ID |

> **Security:** Never commit `.env` to git. It is already in `.gitignore`.

---

## Step 3 — Run Locally

### Simple run (foreground)

```bash
python main.py
```

Press `Ctrl+C` to stop.

### With auto-restart via supervisord

```bash
pip install supervisor
supervisord -c supervisord.conf

# Check status
supervisorctl -c supervisord.conf status

# Tail logs live
supervisorctl -c supervisord.conf tail -f kalshi-bot

# Stop
supervisorctl -c supervisord.conf stop kalshi-bot
```

---

## Step 4 — View P&L Report

```bash
python pnl_report.py
```

This prints a full trade history table from the SQLite database.

---

## Step 5 — Deploy to Railway

### Prerequisites

```bash
npm install -g @railway/cli   # Install Railway CLI
railway login
```

### Deploy

```bash
railway init          # Link or create a Railway project
railway up            # Deploy (uses railway.toml automatically)
```

### Set environment variables on Railway

In the Railway dashboard → your project → **Variables**, add:

- `KALSHI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Or via CLI:

```bash
railway variables set KALSHI_API_KEY=your_key_here
railway variables set TELEGRAM_BOT_TOKEN=your_token_here
railway variables set TELEGRAM_CHAT_ID=your_chat_id_here
```

Railway will automatically restart the bot on crashes (configured in `railway.toml`).

---

## Configuration Reference

All configurable via `.env`:

| Variable | Default | Description |
|---|---|---|
| `KALSHI_API_KEY` | *(required)* | Kalshi API key |
| `TELEGRAM_BOT_TOKEN` | *(optional)* | Telegram bot token |
| `TELEGRAM_CHAT_ID` | *(optional)* | Telegram chat/user ID |
| `SCAN_INTERVAL_SEC` | `30` | Seconds between full market scans |
| `DB_PATH` | `trades.db` | Path to SQLite database |
| `LOG_FILE` | `bot.log` | Path to rotating log file |

---

## Trading Parameters

Edit these constants in `bot.py` to tune the strategy:

| Constant | Default | Meaning |
|---|---|---|
| `BUY_THRESHOLD_CENTS` | `97` | Buy YES if ask >= this |
| `SELL_THRESHOLD_CENTS` | `65` | Sell YES if ask drops below this |
| `MAX_POSITIONS` | `10` | Maximum concurrent open positions |
| `MIN_VOLUME` | `50` | Minimum market volume to enter |
| `MIN_HOURS_TO_EXPIRY` | `1` | Skip markets expiring sooner |
| `CONTRACTS_PER_TRADE` | `1` | Contracts per order |

---

## Risk Warning

This is an automated trading bot for real money markets. Kalshi markets are regulated prediction markets. Past performance does not guarantee future results. Use at your own risk. Always test with small amounts first.
