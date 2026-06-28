# AI Trading Lab

> Open-source AI automation experiment: live trading, multi-layer risk control, daily postmortems, and data-driven strategy evolution on Binance Futures.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20USD%E2%93%88--M-F0B90B?logo=binance)
![Strategy](https://img.shields.io/badge/Strategy-RSI%20%2B%20Funding%20Rate-orange)
![Status](https://img.shields.io/badge/Status-Live%20Experiment-red)

English | [中文](README-Chinese.md)

---

## Why this repo exists

Most trading-bot repos on GitHub show a backtest curve and disappear. This one does the opposite:

- **Real money, real losses, real postmortems** — every trade is logged in SQLite, every failure is written up
- **9 layers of risk control** — exchange-side SL/TP, daily/weekly loss caps, streak cooldown, kill-switch
- **Data-driven parameter tuning** — 60-day BTC replay with live-like fees/slippage, not gut feeling
- **Honest about losing** — v4 lost 49 USDT, v7 lost 16 USDT under Wilder indicators; we show why and how each version was fixed
- **v9: funding rate signal** — Binance perpetual funding rate z-score as confluence filter + standalone signal

This is not a "get rich" bot. It's an engineering experiment: **can an automated system survive a noisy market without blowing up?**

> ⚠️ Cryptocurrency derivatives and leverage can cause total loss of capital. This project is for engineering research only. Not investment advice.

---

## Backtest results: v7 → v8

3-window BTCUSDT 5m replay with live-like fees and slippage, using Wilder-smoothed RSI/ADX (the same indicators the live trader uses):

| Metric | v7 (old) | v8 (current) |
|---|---|---|
| RSI thresholds | 15 / 85 | 20 / 80 |
| SL / TP | 1.5% / 3.0% | 1.5% / 3.0% |
| Leverage / margin | 5x / 15 USDT | 5x / 15 USDT |
| **3-window total PnL** | **-16.70 USDT** | **+12.88 USDT** |
| **Profit factor** | 0.61 | 1.18 |
| **Win rate** | 38% | 42% |
| **Windows positive** | 0 / 3 | 3 / 3 |

Key insight: **v7's RSI 15/85 looked profitable under SMA-based RSI but was a LOSER under Wilder smoothing** (PF 0.61, all 3 windows negative). The old sweep script used a different RSI implementation than the live trader, producing false-positive backtest results. v8 (RSI 20/80) is the only configuration that is positive across all 3 test windows.

Full analysis in [`策略归档.md`](策略归档.md).

---

## v9: Funding rate signal

Binance perpetual futures settle funding rates every 8 hours. When funding is extremely positive, longs pay shorts — the market is overcrowded long. When extremely negative, shorts pay longs. This is a contrarian signal.

**Two modes:**

1. **Confluence filter** — RSI BUY only fires when funding z-score < 0 (shorts paying); RSI SELL only fires when z > 0 (longs paying). If RSI and funding disagree, signal is downgraded to FLAT.

2. **Standalone signal** — When |z| > 3.0 (extreme), generates a signal without needing RSI. z > 3 → SELL (longs overcrowded), z < -3 → BUY (shorts overcrowded). Overrides the trend filter but never the risk filter.

**Config:**

```yaml
funding_rate_enabled: true
funding_zscore_period: 30        # 30 × 8h = 10 days lookback
funding_zscore_threshold: 2.0    # confluence gate
funding_zscore_extreme: 3.0      # standalone trigger
```

**API:** `GET /fapi/v1/fundingRate` (history) + `GET /fapi/v1/premiumIndex` (current). Cached 5 min to avoid rate limits.

Backtest: `python scripts/backtest_funding_rate.py` (run on Windows with proxy, or `--csv` with pre-fetched data).

---

## System architecture

```text
Binance USDⓈ-M Futures
    │
    ▼
┌──────────────────┐
│  Strategy Engine  │  RSI(7) on 5m candles + funding rate z-score
│  rsi_extremes_5m  │  Confluence: RSI confirmed by funding direction
│  + funding signal │  Standalone: extreme funding z > 3 overrides RSI
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
| 1 | Position size cap | One trade can't consume the account | `target_position_usdt: 15` |
| 2 | Leverage cap | Prevents small moves from causing liquidation | `leverage: 5` |
| 3 | Exchange-side SL | Binance closes position even if bot is offline | `stop_loss_pct: 0.015` |
| 4 | Exchange-side TP | Binance takes profit even if bot is offline | `take_profit_pct: 0.030` |
| 5 | Daily loss cap | Stops new entries after a bad day | `daily_loss_pct: 0.25` |
| 6 | Weekly loss cap | Prevents compounding losses across days | `weekly_loss_pct: 0.40` |
| 7 | Streak cooldown | 3 consecutive losses → 24h pause | `streak_loss_count: 3` |
| 8 | Post-trade cooldown | Wait 1h after any close before re-entering | `cooldown_bars_after_trade: 12` |
| 9 | Manual kill-switch | `touch data/KILLSWITCH` → instant stop | — |

More detail: [`docs/risk-control.md`](docs/risk-control.md)

---

## Quick start

### Option A: Interactive setup with [Hermes Agent](https://hermes-agent.nousresearch.com)

If you use Hermes Agent, there's a one-command interactive installer:

```bash
# Copy the skill to your Hermes skills directory
cp -r skill/deploy-binance-trader-bot ~/.hermes/skills/devops/

# Then just tell Hermes: "deploy binance-trader-bot"
# It will guide you through clone, API keys, config, and verification interactively
```

### Option B: Manual setup

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
# config/trader.yaml — v9 (current)

symbol: BTCUSDT
strategy_name: rsi_extremes_5m
rsi_period: 7
rsi_oversold: 20.0        # v8: was 15.0 (v7)
rsi_overbought: 80.0       # v8: was 85.0 (v7)

kline_interval: 5m
poll_seconds: 60
warmup_bars: 210           # EMA200 + buffer
cooldown_bars_after_trade: 12  # ~1h cooldown after any close

target_position_usdt: 15.0
leverage: 5
stop_loss_pct: 0.015       # 1.5%
take_profit_pct: 0.030     # 3.0% — R:R = 2:1

daily_loss_pct: 0.25
weekly_loss_pct: 0.40
streak_cooldown_hours: 24
streak_loss_count: 3

# v9: Funding rate signal / 资金费率信号
funding_rate_enabled: true
funding_zscore_period: 30        # ~10 days of 8h funding intervals
funding_zscore_threshold: 2.0    # confluence filter threshold
funding_zscore_extreme: 3.0      # standalone signal threshold
```

---

## Strategy evolution

| Version | RSI | SL/TP | Result | Lesson |
|---|---|---|---|---|
| v1 | 30/70, 15m | 1.0%/1.5% | Too few signals | Timeframe too slow for small account |
| v2 | 35/65, 5m | 1.0%/1.5% | Better frequency | Short side added |
| v3 | 20/80, 5m | 1.0%/1.5% | Overtrading | Still too loose |
| v4 | 20/80, 5m | 0.6%/0.9% | -49.55 USDT / 60d | Tight SL + loose RSI = death by fees |
| v5 | 12/88, 5m | 0.5%/1.0% | +24.90 USDT / 60d | Strict thresholds + cooldown = quality over quantity |
| v6 | 15/85, 5m | 0.8%/1.6% | Marginal | Wider RSI band increased frequency but not PnL |
| v7 | 15/85, 5m | 1.5%/3.0% | -16.70 USDT / 3-window | LOSER under Wilder indicators (PF 0.61). Old SMA-based sweep gave false positive |
| **v8** | **20/80, 5m** | **1.5%/3.0%** | **+12.88 USDT / 3-window** | **Wilder backtest verified: only config positive in all 3 windows (avgPF 1.18)** |
| **v9** | **20/80, 5m + funding z-score** | **1.5%/3.0%** | **Backtest pending (needs funding history)** | **Funding rate confluence filter + standalone extreme signal. Sharpe 2.09 reported in literature** |

Full history: [`策略归档.md`](策略归档.md)

---

## Project structure

```text
binance-trader-bot/
├── config/trader.yaml           # Strategy + risk parameters / 策略与风控参数
├── trader/
│   ├── exchange.py              # Binance IO layer (frozen) / Binance IO 层（冻结）
│   ├── config.py                # Config dataclass + YAML loader / 配置加载
│   ├── models.py                # Position dataclass / 持仓数据结构
│   └── paths.py                 # Path constants / 路径常量
├── gridtrader/quant/
│   ├── indicators.py            # RSI, EMA, ADX, funding_zscore / 技术指标
│   ├── strategies.py            # RSI mean-reversion signal / RSI 均值回归策略
│   ├── backtest.py              # Backtest engine / 回测引擎
│   ├── hmac_client.py           # Signed request helper / 签名请求工具
│   └── storage.py               # SQLite trade log / SQLite 交易记录
├── scripts/
│   ├── live_trader.py           # Production entrypoint / 生产入口
│   ├── sweep_multi.py           # Multi-strategy backtest sweep / 多策略回测扫描
│   ├── run_backtest.py          # Single backtest runner / 单次回测
│   ├── backtest_exit_logic.py   # Exit logic backtest / 退出逻辑回测
│   ├── backtest_funding_rate.py # v9 funding rate backtest / 资金费率回测
│   ├── list_algo_orders.py      # Inspect exchange SL/TP / 查看交易所端止损止盈
│   ├── place_safety_stop.py     # Emergency protective order / 紧急保护单
│   ├── check_open_orders.py     # Quick position check / 快速持仓检查
│   ├── trade_watchdog.py        # Monitor running bot / 监控运行中的机器人
│   ├── positions_futures.py     # Position viewer / 持仓查看
│   ├── transfer_to_futures.py   # Spot→Futures transfer / 现货转合约
│   ├── fetch_klines.py          # Kline data fetcher / K线数据获取
│   └── ping.py                  # API connectivity test / API连通性测试
├── tests/
│   ├── test_exchange_contract.py  # API boundary tests / API 合约测试
│   ├── test_indicators.py         # Indicator math tests / 指标计算测试
│   ├── test_strategies.py         # Strategy signal tests / 策略信号测试
│   └── test_funding_rate.py       # v9 funding rate tests / 资金费率测试
├── reports/                     # Daily postmortems / 每日复盘
├── docs/
│   ├── index.html               # GitHub Pages landing / GitHub Pages 首页
│   └── risk-control.md          # Risk design notes / 风控设计说明
├── 架构说明.md                  # Architecture / 架构说明
├── 策略归档.md                  # Strategy history / 策略版本历史
├── 安全策略.md                  # Security policy / 安全策略
├── 贡献指南.md                  # Contributing guide / 贡献指南
├── 路线图.md                    # Roadmap / 路线图
├── 视频教程.md                  # Video tutorial / 视频教程
├── 使用手册.md                  # User manual / 使用手册
└── 免责声明.md                  # Disclaimer / 免责声明
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

## Credits

- Binance API behavior follows official USDⓈ-M Futures documentation
- Built from scratch as an engineering experiment in automated futures trading

⭐ If this project helped you learn something, consider giving it a star.
