---
name: deploy-binance-trader-bot
description: "Use when deploying or setting up the binance-trader-bot — a Binance USDⓈ-M Futures RSI + funding rate trading bot. Guides the user through interactive installation: clone, venv, API keys, config, dry-run verification, and live launch with full risk warnings."
version: 1.0.0
author: jiangkaili
license: MIT
metadata:
  hermes:
    tags: [trading, binance, futures, deployment, setup, rsi, automation]
    related_skills: []
---

# Deploy Binance Trader Bot

Interactive deployment of the [binance-trader-bot](https://github.com/jiangkaili/binance-trader-bot) — an open-source Binance USDⓈ-M Futures RSI + funding rate z-score trading bot with 9-layer risk control.

> ⚠️ This bot trades real money with leverage on cryptocurrency futures. It can lose money. Read every warning carefully.

## When to Use

- User asks to "install", "deploy", "set up", or "run" the binance-trader-bot
- User wants to try the Binance futures trading bot
- User found the GitHub repo and wants help getting started

**Don't use for:**
- General Binance API questions (not bot-specific)
- Modifying the bot's strategy code (use the repo's contributing guide)
- Investment advice (this is engineering tooling, not financial advice)

## Prerequisites

Before starting, verify the user's environment:

```bash
python3 --version   # must be 3.10+
git --version
```

If Python is missing or too old, tell the user to install Python 3.10+ first. On Windows, recommend `python.org` or `winget install Python.Python.3.12`.

## Interactive Setup Flow

Follow these steps in order. Use `clarify` at each decision point marked with **[ASK]**.

### Step 1: Clone & Install

```bash
git clone https://github.com/jiangkaili/binance-trader-bot.git
cd binance-trader-bot
python3 -m venv .venv
```

**Platform-specific venv activation:**
- Linux/macOS: `source .venv/bin/activate`
- Windows (PowerShell): `.venv\Scripts\Activate.ps1`
- Windows (CMD): `.venv\Scripts\activate.bat`

```bash
pip install -r requirements.txt
```

Dependencies: `requests`, `pyyaml`, `pandas`, `numpy`, `pytest`.

### Step 2: Choose Environment  **[ASK]**

Ask the user:

> You need Binance API keys to run the bot. Which environment do you want to use?
> 1. **Testnet** (recommended) — paper trading with fake USDT, no real money at risk
> 2. **Production** — real money, real losses, real leverage

If the user is new to trading bots or just exploring, strongly recommend testnet first.

**Testnet keys:** https://testnet.binancefuture.com — log in with Binance account, create a key there.
**Production keys:** https://www.binance.com/en/my/settings/api-management — create a key with "Futures Trading" permission only (disable Spot, Withdrawals). Enable IP whitelist.

> 🔐 Security: Never share API keys. Never commit `.env` to git (it's in `.gitignore`). Enable IP whitelist on production keys. Disable withdrawal permission.

### Step 3: Proxy Configuration  **[ASK]**

Ask the user:

> Can you access Binance directly, or do you need a proxy/VPN?
> 1. **Direct connection** — Binance is accessible from my location
> 2. **I need a proxy** — Binance is geo-blocked or I use a VPN

If proxy is needed, ask for the proxy host and port (e.g., `127.0.0.1:7890` for Clash, `127.0.0.1:1080` for some VPNs, `127.0.0.1:12000` for iKuuu).

### Step 4: Create .env File

Based on the user's answers, create `.env` in the project root:

```bash
cp .env.example .env
chmod 600 .env   # Linux/macOS only — restricts read access
```

Then fill in the values. The `.env` file format:

```
BINANCE_API_KEY=<user's key>
BINANCE_API_SECRET=<user's secret>
USE_TESTNET=<true for testnet, false for production>
PROXY_HOST=<proxy host or blank>
PROXY_PORT=<proxy port or 0>
DEFAULT_SYMBOL=BTCUSDT
MARKET=futures
DB_PATH=./data/trades.db
CACHE_DIR=./data/cache
LOG_LEVEL=INFO
```

**Important:** Ask the user to paste their API key and secret. Write them to `.env` using the `write_file` tool. Never echo the keys back in the conversation after writing them.

### Step 5: Verify Connectivity

Run the ping test to confirm the bot can reach Binance:

```bash
.venv/bin/python scripts/ping.py    # Linux/macOS
.venv\Scripts\python.exe scripts\ping.py    # Windows
```

If this fails with `ConnectionError`:
- Check proxy settings in `.env`
- Verify API keys are correct
- Ensure "Futures Trading" permission is enabled on the key
- On Windows, check if the proxy is actually running

### Step 6: Review Configuration

The strategy and risk parameters live in `config/trader.yaml`. Show the user the current config and explain the key fields:

| Parameter | Default | Meaning |
|---|---|---|
| `symbol` | BTCUSDT | Trading pair |
| `leverage` | 5 | Leverage multiplier (higher = more risk) |
| `target_position_usdt` | 15.0 | Margin per trade in USDT |
| `rsi_oversold` | 20.0 | RSI below this → long signal |
| `rsi_overbought` | 80.0 | RSI above this → short signal |
| `funding_rate_enabled` | true | v9: enable funding rate signal |
| `funding_zscore_extreme` | 3.0 | v9: standalone signal threshold |
| `stop_loss_pct` | 0.015 | Stop loss at 1.5% price move |
| `take_profit_pct` | 0.030 | Take profit at 3.0% price move |
| `daily_loss_pct` | 0.25 | Stop trading after 25% daily loss |
| `weekly_loss_pct` | 0.40 | Stop trading after 40% weekly loss |
| `cooldown_bars_after_trade` | 12 | Wait 12 × 5min bars after each trade |

**[ASK]** Ask the user if they want to adjust any parameters. For first-time users, recommend keeping defaults.

> ⚠️ Risk warning: `leverage` and `target_position_usdt` directly control how much money is at risk per trade. At 5x leverage with 15 USDT margin, a 1.5% stop-loss costs ~1.13 USDT. Higher leverage or larger position = larger potential loss.

### Step 7: Run Tests

Verify the code is healthy:

```bash
.venv/bin/python -m pytest tests/ -v    # Linux/macOS
.venv\Scripts\python.exe -m pytest tests/ -v    # Windows
```

Expect: 51 tests, all passing. If any fail, do not proceed — report the failures.

### Step 8: Dry-Run Verification

Run the bot in dry-run mode. This connects to Binance, reads real market data, generates signals, but does NOT place orders:

```bash
.venv/bin/python scripts/live_trader.py --dry-run --env-file .env    # Linux/macOS
.venv\Scripts\python.exe scripts\live_trader.py --dry-run --env-file .env    # Windows
```

Let it run for 2-3 minutes. Look for:
- `STARTED` line with correct parameters (leverage, target, SL, TP)
- No `ConnectionError` (means proxy/network is working)
- Signal updates every ~60 seconds
- `FLAT` signals are normal (bot only trades at RSI extremes)

If you see errors, check:
- `KILLSWITCH active` → delete `data/KILLSWITCH` file
- `ConnectionError` → fix proxy settings
- `Invalid API-key` → check keys in `.env`

### Step 9: Going Live (Production Only)

> 🛑 STOP. Before going live, confirm with the user:
> 1. They understand this uses real money
> 2. They can afford to lose the entire position
> 3. They have tested in dry-run mode first
> 4. The parameters in `config/trader.yaml` match their risk tolerance

**[ASK]** Ask the user to explicitly confirm they want to trade with real money. Do not proceed without explicit confirmation.

To start live trading:

```bash
.venv/bin/python scripts/live_trader.py --env-file .env    # Linux/macOS
.venv\Scripts\python.exe scripts\live_trader.py --env-file .env    # Windows
```

For long-running deployment, use a process manager or screen/tmux:

```bash
# Using tmux (recommended for SSH servers)
tmux new -s trader
.venv/bin/python scripts/live_trader.py --env-file .env
# Ctrl+B then D to detach
# tmux attach -t trader to reattach
```

### Step 10: Monitoring & Safety

After the bot is running, explain these monitoring tools:

**Check position and orders:**
```bash
.venv/bin/python scripts/check_open_orders.py --env-file .env
.venv/bin/python scripts/list_algo_orders.py --env-file .env
```

**Watchdog (monitor running bot):**
```bash
.venv/bin/python scripts/trade_watchdog.py
```

**Emergency stop — create a KILLSWITCH file:**
```bash
touch data/KILLSWITCH    # Bot will stop opening new positions
# Remove to resume: rm data/KILLSWITCH
```

**Emergency close — place a safety stop:**
```bash
.venv/bin/python scripts/place_safety_stop.py --env-file .env
```

## Common Pitfalls

1. **WSL can't reach Binance.** WSL does not inherit Windows proxy settings. If running in WSL, either configure the proxy in `.env` pointing to the Windows host IP, or run natively on Windows.

2. **KILLSWITCH file blocking trades.** If `data/KILLSWITCH` exists, the bot refuses to open positions. Delete it: `rm data/KILLSWITCH`.

3. **Testnet vs Production keys.** Testnet keys are different from production keys. `USE_TESTNET=true` requires testnet keys; `USE_TESTNET=false` requires production keys. Mixing them causes `Invalid API-key` errors.

4. **API key permissions.** The key must have "Futures Trading" (USDⓈ-M) permission enabled. Spot-only keys will fail silently on futures endpoints. Never enable "Withdrawals" permission.

5. **Windows path issues.** On Windows, use backslashes for venv activation (`.venv\Scripts\activate`) but forward slashes in Python paths. The bot code uses `pathlib` and works on both platforms.

6. **Exchange-side SL/TP persists after bot crash.** If the bot dies, the STOP_MARKET and TAKE_PROFIT_MARKET orders stay on Binance. This is by design — your position is still protected. Restart the bot and it will detect the existing position.

7. **Insufficient balance.** The bot needs at least `target_position_usdt` (15 USDT by default) plus a fee buffer (~1 USDT) in the futures wallet. Transfer from spot if needed: `python scripts/transfer_to_futures.py`.

8. **Fees eat profits at high leverage.** At 20x leverage, taker fees (0.04% × 2 sides) cost 1.6% of margin per round trip. At 5x, fees drop to 0.4%. This is why the default is 5x.

## Verification Checklist

After setup is complete, verify:

- [ ] `git clone` succeeded, repo is in `binance-trader-bot/`
- [ ] `.venv` created and activated
- [ ] `pip install -r requirements.txt` completed without errors
- [ ] `.env` file created with correct API keys and environment setting
- [ ] `python scripts/ping.py` returns successfully (connectivity confirmed)
- [ ] `python -m pytest tests/ -v` shows 51 passed
- [ ] `python scripts/live_trader.py --dry-run` starts without errors and shows correct parameters
- [ ] `config/trader.yaml` parameters reviewed and understood
- [ ] User understands the risk warning before going live
- [ ] User knows how to create a KILLSWITCH for emergency stop
