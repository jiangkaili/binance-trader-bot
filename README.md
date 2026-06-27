# AI Trading Lab

> Open-source AI automation experiment: live trading, multi-layer risk control, daily postmortems, and data-driven strategy evolution on Binance Futures.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20USD%E2%93%88--M-F0B90B?logo=binance)
![Strategy](https://img.shields.io/badge/Strategy-RSI%20Mean%20Reversion-orange)
![Status](https://img.shields.io/badge/Status-Live%20Experiment-red)

English | [中文](README-Chinese.md)

---

## Why this repo exists

Most trading-bot repos on GitHub show a backtest curve and disappear. This one does the opposite:

- **Real money, real losses, real postmortems** — every trade is logged in SQLite, every failure is written up
- **9 layers of risk control** — exchange-side SL/TP, daily/weekly loss caps, streak cooldown, kill-switch
- **Data-driven parameter tuning** — 60-day BTC replay with live-like fees/slippage, not gut feeling
- **Honest about losing** — v4 strategy lost 49 USDT in 60 days; we show why and how v5 fixed it

This is not a "get rich" bot. It's an engineering experiment: **can an automated system survive a noisy market without blowing up?**

> ⚠️ Cryptocurrency derivatives and leverage can cause total loss of capital. This project is for engineering research only. Not investment advice.

---

## Backtest results: v4 → v5

60-day BTCUSDT 5m replay with live-like fees and slippage:

| Metric | v4 (old) | v5 (current) |
|---|---|---|
| RSI thresholds | 20 / 80 | 12 / 88 |
| SL / TP | 0.6% / 0.9% | 0.5% / 1.0% |
| Post-trade cooldown | none | 12 bars (~1h) |
| **Total trades** | 219 | 69 |
| **Win rate** | 42.5% | 52.2% |
| **Total PnL** | **-49.55 USDT** | **+24.90 USDT** |
| **Per-trade expectancy** | -0.226 | +0.361 |

Key insight: **looser RSI thresholds produced more trades but worse quality**. Tightening to 12/88 cut trade count by 68% and flipped PnL from negative to positive. Adding a 1-hour post-trade cooldown prevented clustered re-entries in RSI chop zones.

Full analysis in [`策略归档.md`](策略归档.md).

---

## System architecture

```text
Binance USDⓈ-M Futures
    │
    ▼
┌──────────────────┐
│  Strategy Engine  │  RSI(7) on 5m candles
│  rsi_extremes_5m  │  Long when RSI < oversold, Short when RSI > overbought
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Risk Manager     │  ✅ Position size cap
│  (9 layers)       │  ✅ Leverage cap
│                    │  ✅ Exchange-side SL/TP (algoOrder)
│                    │  ✅ Code-side SL/TP (backup)
│                    │  ✅ Daily loss cap (25%)
│                    │  ✅ Weekly loss cap (40%)
│                    │  ✅ Streak cooldown (3 losses → 24h)
│                    │  ✅ Post-trade cooldown (12 bars)
│                    │  ✅ Manual kill-switch
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Execution Layer  │  Market orders + algoOrder SL/TP
│  (exchange.py)    │  Crash-resistant: protective orders
│                    │  stay on Binance even if bot dies
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  State + Journal  │  SQLite trades.db
│                    │  live_trader.state (JSON)
│                    │  Daily reports + postmortems
└──────────────────┘
```

The critical design choice: **strategy code changes often, but the exchange IO layer is a frozen contract.** See [`架构说明.md`](架构说明.md).

---

## Risk control — 9 layers

| # | Layer | What it does | Config |
|---|---|---|---|
| 1 | Position size cap | One trade can't consume the account | `target_position_usdt: 25` |
| 2 | Leverage cap | Prevents small moves from causing liquidation | `leverage: 10` |
| 3 | Exchange-side SL | Binance closes position even if bot is offline | `stop_loss_pct: 0.005` |
| 4 | Exchange-side TP | Binance takes profit even if bot is offline | `take_profit_pct: 0.010` |
| 5 | Daily loss cap | Stops new entries after a bad day | `daily_loss_pct: 0.25` |
| 6 | Weekly loss cap | Prevents compounding losses across days | `weekly_loss_pct: 0.40` |
| 7 | Streak cooldown | 3 consecutive losses → 24h pause | `streak_loss_count: 3` |
| 8 | Post-trade cooldown | Wait 1h after any close before re-entering | `cooldown_bars_after_trade: 12` |
| 9 | Manual kill-switch | `touch data/KILLSWITCH` → instant stop | — |

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
# Edit .env — add your Binance API key (Futures permission)
# NEVER commit .env

# Strategy and risk parameters:
$EDITOR config/trader.yaml
```

### 3. Run safely first

```bash
# Dry run: real market data, no real orders
python scripts/live_trader.py --dry-run
```

Only go live after you understand the code, API permissions, and loss limits:

```bash
# Live mode: real orders, real money, real losses
python scripts/live_trader.py
```

---

## Configuration

All strategy and risk parameters in one flat YAML file — easy to audit:

```yaml
# config/trader.yaml — v5 (current)

symbol: BTCUSDT
strategy_name: rsi_extremes_5m
rsi_period: 7
rsi_oversold: 12.0        # v5: was 20.0 — tighter = fewer false signals
rsi_overbought: 88.0       # v5: was 80.0

kline_interval: 5m
poll_seconds: 60
cooldown_bars_after_trade: 12  # v5: ~1h cooldown after any close

target_position_usdt: 25.0
leverage: 10
stop_loss_pct: 0.005      # v5: was 0.006
take_profit_pct: 0.010    # v5: was 0.009 — R:R now 2:1

daily_loss_pct: 0.25
weekly_loss_pct: 0.40
streak_cooldown_hours: 24
streak_loss_count: 3
```

---

## Strategy evolution

| Version | RSI | SL/TP | Result | Lesson |
|---|---|---|---|---|
| v1 | 30/70, 15m | 1.0%/1.5% | Too few signals | Timeframe too slow for small account |
| v2 | 35/65, 5m | 1.0%/1.5% | Better frequency | Short side added |
| v3 | 20/80, 5m | 1.0%/1.5% | Overtrading | Still too loose |
| v4 | 20/80, 5m | 0.6%/0.9% | -49.55 USDT / 60d | Tight SL + loose RSI = death by fees |
| **v5** | **12/88, 5m** | **0.5%/1.0%** | **+24.90 USDT / 60d** | **Strict thresholds + cooldown = quality over quantity** |

Full history: [`策略归档.md`](策略归档.md)

---

## Project structure

```text
binance-trader-bot/
├── config/trader.yaml           # All strategy + risk parameters
├── trader/
│   ├── exchange.py              # Frozen Binance IO layer (contract)
│   ├── trader.py                # Trading loop / policy layer
│   ├── risk.py                  # 9-layer risk manager
│   ├── config.py                # Config dataclass + YAML loader
│   ├── state.py                 # PnL persistence + state dump
│   └── models.py                # Position dataclass
├── scripts/
│   ├── live_trader.py           # Production entrypoint
│   ├── list_algo_orders.py      # Inspect exchange-side SL/TP
│   ├── place_safety_stop.py     # Emergency protective order
│   └── check_open_orders.py     # Quick position/order check
├── tests/
│   ├── test_exchange_contract.py  # API boundary tests
│   └── test_trader_v2.py          # Config + risk gate tests
├── reports/                     # Daily postmortems
├── docs/
│   ├── index.html               # GitHub Pages landing
│   └── risk-control.md          # Risk design notes
├── 架构说明.md              # IO/strategy boundary contract
├── 策略归档.md          # Strategy version history
└── 免责声明.md                # Financial disclaimer
```

---

## Testing

```bash
pytest tests/ -q
```

Exchange contract tests guard against accidentally moving SL/TP back to the wrong API endpoint — the most dangerous regression in this codebase.

---

## Contributing

Good contributions:

- Safer risk-control rules
- Strategy research with **honest losing results included**
- Exchange API contract tests
- Documentation that prevents credential leaks or unsafe live trading

Please do NOT open issues asking for guaranteed-profit settings, signals, or financial advice.

---

## Disclaimer

This repository is for educational and engineering research only. Not financial advice, investment advice, or a recommendation to trade any instrument. Cryptocurrency futures and leveraged derivatives are extremely risky and may result in total loss of capital.

## License

MIT License — see [`LICENSE`](LICENSE).

## Credits

- Original grid-trading framework by [51bitquant](https://github.com/51bitquant)
- Live execution, risk control, reporting, and Binance Algo Order integration built on top
- Binance API behavior follows official USDⓈ-M Futures documentation

⭐ If this project helped you learn something, consider giving it a star.
