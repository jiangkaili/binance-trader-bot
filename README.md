# AI Trading Lab

> Open-source AI automation experiment for live trading, risk control, daily postmortems, and strategy evolution.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20USD%E2%93%88--M-F0B90B?logo=binance)
![Focus](https://img.shields.io/badge/Focus-Risk%20Control-purple)
![Status](https://img.shields.io/badge/Experiment-Live%20Automation-red)

English | [中文](README-Chinese.md)

---

## What this repo is

This is **not** a "get rich" trading bot.

It is a public engineering experiment: can an automated system operate in a noisy, high-risk market environment without losing control?

The project focuses on:

- exchange-side risk protection that survives bot crashes
- transparent daily reports instead of cherry-picked screenshots
- strategy version history and failure postmortems
- reproducible logs and SQLite trade records
- small-capital live experimentation with hard risk limits

If you are interested in AI agents, automation reliability, trading infrastructure, or risk-control design, this repo is meant to be inspected, challenged, and improved.

> Financial risk notice: cryptocurrency derivatives and leverage can cause total loss of capital. This project is for engineering research and education only. It is not investment advice, trading advice, or a signal service.

---

## Current experiment snapshot

The canonical status source is [`reports/README.md`](reports/README.md). The table below is intentionally conservative and points to committed artifacts rather than screenshots.

| Item | Value |
|---|---|
| Experiment | AI-assisted live trading automation lab |
| Market | Binance USDⓈ-M Futures |
| Primary symbol | BTCUSDT |
| Strategy family | RSI extreme mean reversion on 5m candles |
| Risk posture | Small-capital experiment, hard stop-loss / take-profit, daily and weekly caps |
| Latest committed report | [`reports/README.md`](reports/README.md) |
| Strategy history | [`STRATEGY_ARCHIVE.md`](STRATEGY_ARCHIVE.md) |
| Architecture contract | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Risk-control notes | [`docs/risk-control.md`](docs/risk-control.md) |

### Why daily reports matter

Most trading-bot repositories show a backtest curve and stop there. This one treats the running bot as a production system and keeps dated reports with:

- runtime status and heartbeat observations
- account / position snapshot at report time
- every closed trade recorded in SQLite
- strategy reflection and parameter changes
- risk events: stop-loss, kill-switch, cooldown, margin errors
- bug fixes and postmortems when the system fails

Start here: [`reports/README.md`](reports/README.md)

---

## System architecture

```text
Market Data
   │
   ▼
Strategy Engine ──────────────┐
   │ RSI / indicators          │
   ▼                           │
Risk Manager                   │
   │ position cap              │
   │ daily / weekly loss cap   │
   │ streak cooldown           │
   │ manual kill-switch        │
   ▼                           │
Execution Layer                │
   │ Binance market orders     │
   │ exchange-side SL / TP     │
   ▼                           │
State + Journal                │
   │ SQLite trades.db          │
   │ live_trader.state         │
   │ logs                      │
   ▼                           │
Daily Reports + Postmortems ◀──┘
```

The important design choice: strategy code is allowed to change often, but the exchange IO layer is treated as a load-bearing contract. See [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Key features

- **Live execution engine** for Binance USDⓈ-M Futures
- **Exchange-side stop-loss and take-profit** via the newer `/fapi/v1/algoOrder` endpoint family
- **Crash-resistant protection**: protective orders remain on Binance if the bot, host, or network dies
- **Config-driven strategy parameters** in `config/trader.yaml`
- **SQLite trade journal** for reproducible analysis
- **Kill-switch, loss caps, and cooldown rules** for bounded failure
- **Dry-run mode** for safe tests with real market data
- **Strategy archive** documenting what changed, why, and what failed
- **Daily report directory** for transparent operational history
- **Contract tests** that guard the exchange API boundary

---

## Risk-control layers

| Layer | Purpose |
|---|---|
| Position size cap | Prevents one trade from consuming the account |
| Leverage cap | Keeps adverse moves from becoming instant liquidation events |
| Exchange-side SL / TP | Binance closes the position even if the bot is offline |
| Code-side SL / TP | Backup close logic while the bot is alive |
| Daily loss cap | Stops new entries after a bad day |
| Weekly loss cap | Stops compounding losses across multiple bad days |
| Losing-streak cooldown | Forces the bot to pause after repeated misses |
| Manual kill-switch | `data/KILLSWITCH` blocks new entries immediately |
| State reconciliation | Prevents the bot from assuming stale local state is true |

More detail: [`docs/risk-control.md`](docs/risk-control.md)

---

## Quick start

### 1. Install

```bash
git clone https://github.com/jiangkaili/binance-trader-bot.git
cd binance-trader-bot

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and add your own Binance API key.
# Never commit .env or any real credentials.

# Strategy and risk parameters live here:
$EDITOR config/trader.yaml
```

### 3. Run safely first

```bash
# Dry run: reads real market data but does not place orders
python scripts/live_trader.py --dry-run
```

Only run live after you understand the code, the exchange permissions, and the loss limits.

```bash
# Live mode: real orders, real money, real losses possible
python scripts/live_trader.py
```

---

## Configuration

Important strategy and risk parameters are intentionally flat YAML keys in [`config/trader.yaml`](config/trader.yaml), so they can be audited easily:

```yaml
symbol: BTCUSDT
strategy_name: rsi_extremes_5m
rsi_period: 7
rsi_oversold: 20.0
rsi_overbought: 80.0
kline_interval: 5m
poll_seconds: 60
target_position_usdt: 25.0
leverage: 10
stop_loss_pct: 0.006
take_profit_pct: 0.009
daily_loss_pct: 0.25
weekly_loss_pct: 0.40
streak_cooldown_hours: 24
streak_loss_count: 3
```

Environment variables live in `.env` and must never be committed:

| Variable | Meaning |
|---|---|
| `BINANCE_API_KEY` | Binance API key with Futures permission |
| `BINANCE_API_SECRET` | Binance API secret |
| `USE_TESTNET` | Use Binance testnet when supported |
| `PROXY_HOST` / `PROXY_PORT` | Optional proxy settings |

---

## Project map

```text
binance-trader-bot/
├── README.md                  # English project front page
├── README-Chinese.md          # Chinese front page
├── ARCHITECTURE.md            # IO / strategy boundary contract
├── STRATEGY_ARCHIVE.md        # Strategy version history and decisions
├── DISCLAIMER.md              # Financial and operational disclaimer
├── SECURITY.md                # Credential handling and vulnerability reporting
├── config/
│   └── trader.yaml            # Strategy and risk parameters
├── trader/
│   ├── exchange.py            # Frozen Binance IO layer
│   ├── trader.py              # Trading loop / policy layer
│   ├── risk.py                # Risk rules
│   └── state.py               # Runtime state helpers
├── scripts/
│   ├── live_trader.py         # Production entrypoint
│   ├── list_algo_orders.py    # Inspect exchange-side protective orders
│   └── place_safety_stop.py   # Attach protective orders if needed
├── reports/
│   └── README.md              # Daily report index
├── docs/
│   ├── index.html             # GitHub Pages landing page
│   └── risk-control.md        # Risk-control design notes
└── tests/
    ├── test_exchange_contract.py
    └── test_trader_v2.py
```

Runtime files such as `.env`, `data/`, logs, and SQLite databases are ignored by git.

---

## Testing

```bash
pytest tests/test_exchange_contract.py -q
pytest tests/test_trader_v2.py -q
pytest tests/ -q
```

The exchange contract tests are especially important. They guard against accidentally moving SL / TP back to the wrong endpoint family.

---

## Contributing

Good contributions are usually in one of these categories:

- safer risk-control rules
- clearer postmortem/report generation
- exchange API contract tests
- strategy research with honest losing results included
- documentation that helps users avoid credential leaks or unsafe live trading

Please do not open issues asking for guaranteed-profit settings, copy-trading signals, or financial advice.

---

## Disclaimer

This repository is for educational and engineering research purposes only. It is not financial advice, investment advice, or a recommendation to trade any instrument. Cryptocurrency futures and leveraged derivatives are extremely risky and may result in total loss of capital. You are responsible for your own keys, trades, losses, taxes, and compliance obligations.

---

## License

MIT License — see [`LICENSE`](LICENSE).

## Credits

- Original grid-trading framework by [51bitquant](https://github.com/51bitquant)
- Live trading, risk-control, reporting, and Binance Algo Order integration built on top of the original framework
- Binance API behavior follows the official USDⓈ-M Futures documentation
