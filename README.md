# 🚀 Binance Futures Scalper

> Autonomous RSI mean-reversion trading bot for Binance USDⓈ-M Futures — with exchange-side stop-loss protection, crash-proof architecture, and the new 2025 Algo Order API.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Binance](https://img.shields.io/badge/Exchange-Binance%20Futures-F0B90B?logo=binance)
![Status](https://img.shields.io/badge/Status-Live%20Trading-red)

[中文](README-Chinese.md) | English

---

## 📊 Live Trading Report

> **This bot is running live on Binance Futures right now.** Every day a fresh daily report is committed to this repo with full trade history, P&L breakdown, strategy reflection, and any bug fixes shipped that day. No screenshots, no cherry-picked equity curves — just the SQLite database and the commit log.

**📅 Latest report → see top of [`reports/README.md`](reports/README.md)**
**📁 All reports → [`reports/`](reports/)**

### 🔴 Current Status (auto-updated daily)

| Metric | Value |
|---|---|
| **Status** | 🟢 Live trading on Binance Futures (Mainnet) |
| **Symbol** | BTCUSDT @ 20× leverage, 25 USDT target position |
| **Strategy** | RSI(7) extremes mean reversion on 5m klines |
| **Bot-realized PnL** | **+3.34 USDT** over 4 closed trades (100% win rate on bot-managed exits) |
| **First trade** | 2026-06-21 |
| **Latest activity** | See most recent file in [`reports/`](reports/) |

> ⚠️ **Full disclosure**: the SQLite log also contains 2 *manually-handled* exchange-side stop-loss fills (`order_id LIKE 'backfilled_%'`) that hit before a bug fix landed — a combined −8.25 USDT loss. These are excluded from the win-rate figure above because they were not bot-initiated exits. Both rows are still in `data/trades.db` and the daily reports in [`reports/`](reports/) walk through the root cause and the fix that shipped.

### 📈 Why Daily Reports?

Most "trading bot" repos on GitHub show a backtest curve and call it a day. This one ships a **dated markdown report every day the bot trades**, including:

- ✅ **Real account balance** pulled live from Binance Futures API
- ✅ **Every trade** with entry, exit, P&L, exit reason (signal close vs SL/TP vs manual)
- ✅ **Strategy reflection** — what worked, what broke, what the data says about the next parameter tweak
- ✅ **Bug fixes & code changes** committed that day, with reproducible repro steps
- ✅ **Risk events** — kill-switch triggers, streak-cooldowns, margin-insufficient errors

If you want to see how the bot *actually* behaves in production (not just in a notebook), browse the [`reports/`](reports/) directory and read a few days. The trade DB, log files, and source code in this repo all line up — pull the repo and run the same SQL queries the reports use.

---

## ✨ Features

- **Fully autonomous** — polls Binance Futures, generates RSI signals, opens/closes positions, places stop-loss/take-profit orders. No human intervention needed.
- **Crash-proof protection** — stop-loss & take-profit are placed on the exchange (not just in code). If your bot dies, Windows crashes, or your internet drops, Binance still executes them.
- **2025 Algo Order API** — implements Binance's new `/fapi/v1/algoOrder` endpoints (required since Dec 2025, most open-source bots still use the deprecated `/fapi/v1/order` and get `-4120` errors).
- **Multi-layer risk management** — per-trade SL/TP, daily loss cap, weekly loss cap, 3-streak cooldown, auto kill-switch.
- **Both long & short** — RSI extreme breakout signals trigger directional trades in either direction.
- **Dry-run mode** — paper trade with real market data, no orders placed.
- **SQLite trade logging** — every trade recorded for post-hoc analysis.
- **WSL clock drift immunity** — auto-resyncs Binance server time every 30 min (WSL drifts fast).

## 📐 How It Works

```
┌─────────────────────────────────────────────────────────┐
│                    LIVE TRADER BOT                       │
│                                                         │
│  ┌──────────┐   ┌───────────────┐   ┌────────────────┐ │
│  │ Fetch 5m │──▶│  RSI(7) 20/80 │──▶│  Signal: BUY   │ │
│  │ klines   │   │  mean reversion│   │  / SELL / FLAT │ │
│  └──────────┘   └───────────────┘   └───────┬────────┘ │
│                                              │          │
│                    ┌─────────────────────────▼───┐      │
│                    │      Risk Check Engine       │      │
│                    │  • Daily loss cap            │      │
│                    │  • Weekly loss cap           │      │
│                    │  • 3-streak cooldown         │      │
│                    │  • Kill-switch               │      │
│                    └─────────────┬───────────────┘      │
│                                  │                      │
│              ┌───────────────────▼──────────────┐       │
│              │     Binance Futures API           │       │
│              │  POST /fapi/v1/order (MARKET)     │       │
│              │  POST /fapi/v1/algoOrder (SL/TP)  │       │
│              └──────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘

                    Exchange-side protection
         ┌──────────────────────────────────────┐
         │  STOP_MARKET     ← auto-fires if     │
         │  TAKE_PROFIT_    │ price hits SL/TP  │
         │  MARKET          │ (bot crash-proof) │
         └──────────────────────────────────────┘
```

## 🏁 Quick Start

### 1. Prerequisites

- Python 3.10+
- A Binance account with Futures trading enabled
- API key with "Futures Trading" permission (create at [Binance API Management](https://www.binance.com/en/my/settings/api-management))

### 2. Install

```bash
git clone https://github.com/YOUR_USERNAME/binance_grid_trader.git
cd binance_grid_trader

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — paste your Binance API key and secret

# Optionally tune strategy parameters:
# Edit config/trader.yaml
```

### 4. Run

```bash
# Paper trade (no real orders — safe to test):
python scripts/live_trader.py --dry-run

# Live trade (REAL MONEY):
python scripts/live_trader.py

# Or specify a custom env file:
python scripts/live_trader.py --env-file .env.production
```

### 5. Stop

```bash
# Graceful (closes position, then exits):
kill -TERM $(cat data/trader.pid)

# Or Ctrl+C in the terminal
```

## ⚙️ Configuration

### Environment Variables (`.env`)

| Variable | Description | Default |
|---|---|---|
| `BINANCE_API_KEY` | Your Binance Futures API key | *required* |
| `BINANCE_API_SECRET` | Your Binance Futures API secret | *required* |
| `USE_TESTNET` | Use Binance testnet (paper trading) | `false` |
| `PROXY_HOST` | Proxy host (if Binance is geo-blocked) | *empty* |
| `PROXY_PORT` | Proxy port | `0` |

### Strategy Parameters (`config/trader.yaml`)

All trading parameters are in one YAML file. No code changes needed to tune the bot.

```yaml
# Strategy: RSI(7) extreme mean-reversion on 5m bars
symbol: BTCUSDT
kline_interval: 5m
poll_seconds: 60

# Position sizing
target_position_usdt: 25.0   # margin per trade
leverage: 20                  # leverage multiplier

# RSI parameters
rsi_period: 7
rsi_oversold: 20.0            # BUY when RSI crosses back above this
rsi_overbought: 80.0          # SELL when RSI crosses back below this

# Stop-loss / Take-profit (price move %)
stop_loss_pct: 0.01           # -1% → close position
take_profit_pct: 0.01         # +1% → close position

# Risk caps (fraction of starting equity)
daily_loss_pct: 0.25          # stop trading after -25% in one day
weekly_loss_pct: 0.40         # stop trading after -40% in one week
```

## 📊 Strategy: RSI(7) Extreme Mean Reversion

**Logic**: When RSI(7) dips below 20 (oversold extreme) and snaps back, go LONG. When RSI(7) exceeds 80 (overbought extreme) and falls back, go SHORT. Close on opposite signal or when SL/TP is hit.

**Why it works**: RSI extremes on 5m bars indicate short-term exhaustion. The snap-back is a high-probability reversal — but only at the extremes (20/80), not in the neutral zone (45/55) where most RSI bots operate.

### Backtest Results (5 days, BTCUSDT 5m, fees included)

| Strategy | Trades | Win Rate | Net P&L |
|---|---|---|---|
| **RSI(7) 20/80** | 20 | **75%** | **+4.31%** |
| RSI(7) 25/75 | 33 | 63.6% | -2.44% |
| RSI(7) 30/70 | 42 | 57.1% | -3.29% |
| RSI(14) 45/55 | 76 | 51.3% | -3.25% |
| EMA9/21 cross | 68 | — | -5.80% |
| Bollinger revert | 42 | — | -6.10% |
| Donchian breakout | 34 | — | -3.50% |

**Key insight**: Lower frequency = higher win rate. The 20/80 extreme filter eliminates false signals from the chop zone.

## 🛡️ Risk Management

| Layer | Trigger | Action |
|---|---|---|
| **Exchange SL/TP** | Price moves ±1% | Binance auto-closes position (bot-independent) |
| **Code SL/TP** | Price moves ±1% | Bot closes position (backup to exchange) |
| **Daily loss cap** | Daily P&L reaches -25% | Bot stops opening new positions until next day |
| **Weekly loss cap** | Weekly P&L reaches -40% | Bot stops opening new positions until next week |
| **3-streak cooldown** | 3 consecutive losing trades | 24-hour cooldown |
| **Auto kill-switch** | Cumulative loss hits -10% of starting equity | Permanent stop (requires manual reset) |
| **Manual kill-switch** | Create `data/KILLSWITCH` file | Bot refuses to trade |

## 🔌 API Implementation

This bot uses Binance's **new Algo Order API** (required since December 2025). Most open-source bots haven't migrated yet and get `-4120` errors.

| Operation | Endpoint | Status |
|---|---|---|
| Create conditional order | `POST /fapi/v1/algoOrder` | ✅ Implemented |
| Cancel single order | `DELETE /fapi/v1/algoOrder` | ✅ Implemented |
| Cancel all by symbol | `DELETE /fapi/v1/algoOpenOrders` | ✅ Implemented |
| List open algo orders | `GET /fapi/v1/openAlgoOrders` | ✅ Implemented |

Key parameters: `algoType=CONDITIONAL`, `triggerPrice` (not `stopPrice`), `closePosition=true`.

## 📁 Project Structure

```
binance_grid_trader/
├── .env.example              # Environment variable template
├── config/
│   └── trader.yaml           # Strategy & risk parameters
├── scripts/
│   ├── live_trader.py        # ⭐ Main trading bot
│   ├── positions_futures.py  # Check open positions
│   ├── run_backtest.py       # Backtest strategies
│   ├── sweep_all_15m.py      # Parameter sweep
│   ├── fetch_klines.py       # Download historical data
│   ├── watchdog.sh           # Auto-restart on crash
│   └── ping.py               # API connectivity test
├── gridtrader/
│   ├── quant/
│   │   ├── strategies.py     # Strategy classes (RSI, EMA, Bollinger...)
│   │   ├── indicators.py     # Technical indicators
│   │   ├── backtest.py       # Backtesting engine
│   │   ├── risk.py           # Risk calculations
│   │   ├── storage.py        # SQLite trade storage
│   │   └── hmac_client.py    # Signed Binance API client
│   └── trader/               # GUI + gateway (legacy grid mode)
├── tests/                    # Pytest test suite
└── requirements.txt
```

## 🖥️ Running as a Daemon

### Linux / WSL

```bash
nohup python scripts/live_trader.py > data/stdout.log 2> data/stderr.log &
echo $! > data/trader.pid

# With watchdog (auto-restart on crash):
bash scripts/watchdog.sh &
```

### Windows (PowerShell)

```powershell
Start-Process -FilePath "python" -ArgumentList "scripts/live_trader.py" `
    -WorkingDirectory "C:\trader" -WindowStyle Minimized
```

## 📈 Monitoring

The bot writes several files for monitoring:

| File | Description |
|---|---|
| `data/live_trader.log` | Human-readable log with timestamps |
| `data/live_trader.state` | JSON snapshot of current state (position, P&L, signal) |
| `data/trades.db` | SQLite database of all trades and events |
| `data/pnl_state.json` | Persisted daily/weekly P&L (survives restarts) |

Quick check:
```bash
# Current state
cat data/live_trader.state | python -m json.tool

# Recent trades
sqlite3 data/trades.db "SELECT * FROM trades ORDER BY ts DESC LIMIT 10"

# Last 20 log lines
tail -20 data/live_trader.log
```

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=gridtrader --cov-report=term-missing
```

## 📜 Disclaimer

**This software is for educational purposes only.** Cryptocurrency futures trading with leverage carries substantial risk of loss. This bot can lose all your money. Past backtest performance does not guarantee future results. Use at your own risk. Never trade with money you cannot afford to lose.

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

## 🙏 Credits

- Original grid trading framework by [51bitquant](https://github.com/51bitquant)
- RSI strategy and live trading engine built on top of the gridtrader package
- Algo Order API implementation follows [Binance official docs](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Algo-Order)
