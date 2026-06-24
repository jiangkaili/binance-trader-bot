# AI Trading Lab

> 开源 AI 自动化实验项目：用真实市场测试交易执行、风控、每日复盘和策略演化。

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20USD%E2%93%88--M-F0B90B?logo=binance)
![Focus](https://img.shields.io/badge/Focus-Risk%20Control-purple)
![Status](https://img.shields.io/badge/Experiment-Live%20Automation-red)

中文 | [English](README.md)

---

## 这个项目是什么

这不是一个“自动赚钱机器人”。

它是一个公开的系统工程实验：一个自动化系统能不能在高噪声、高风险的真实市场环境里运行，同时不失控？

项目重点不是喊收益，而是：

- 交易所端风控保护，bot 崩溃也能继续生效
- 每日复盘报告，而不是精选截图
- 策略版本记录和失败复盘
- 可复现的日志与 SQLite 交易记录
- 极小资金实盘实验，并带硬风控边界

如果你关心 AI Agent、自动化系统可靠性、交易基础设施、风控设计，这个仓库是给你看的、挑刺的、改进的。

> 风险提示：数字资产衍生品和杠杆交易可能导致本金全部损失。本项目仅用于工程研究和教育，不构成投资建议、交易建议或任何形式的信号服务。

---

## 当前实验快照

权威状态入口是 [`reports/README.md`](reports/README.md)。下面的表格只做保守概览，所有具体数字以已提交的报告和数据库为准。

| 项目 | 当前值 |
|---|---|
| 实验定位 | AI 辅助的实盘交易自动化实验室 |
| 市场 | Binance USDⓈ-M Futures |
| 主要交易对 | BTCUSDT |
| 策略族 | 5m RSI 极值均值回归 |
| 风控姿态 | 小资金实验，硬止损/止盈，日/周亏损上限 |
| 最新日报 | [`reports/README.md`](reports/README.md) |
| 策略历史 | [`STRATEGY_ARCHIVE.md`](STRATEGY_ARCHIVE.md) |
| 架构边界 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 风控说明 | [`docs/risk-control.md`](docs/risk-control.md) |

### 为什么做每日复盘

GitHub 上大多数 trading bot 仓库放一张回测曲线就结束了。这个项目把 bot 当成一个生产系统来观察，每天沉淀带日期的报告：

- 运行状态和心跳观察
- 报告生成时的账户/持仓快照
- SQLite 里记录的每一笔已平仓交易
- 策略反思和参数变化
- 风控事件：止损、熔断、冷却、保证金错误
- 系统失败时的 bug 修复和 postmortem

入口：[`reports/README.md`](reports/README.md)

---

## 系统架构

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

最重要的设计选择：策略层可以经常改，但交易所 IO 层必须当成承重墙。详见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

---

## 核心特性

- **Binance USDⓈ-M Futures 实盘执行引擎**
- **交易所端止损/止盈**，使用新版 `/fapi/v1/algoOrder` 端点族
- **抗崩溃保护**：bot、主机、网络挂了，保护单仍留在交易所
- **配置驱动策略参数**：集中在 `config/trader.yaml`
- **SQLite 交易日志**：方便复盘和复现分析
- **熔断、亏损上限、连亏冷却**：让失败有边界
- **Dry-run 模式**：真实行情，模拟执行，不下真实订单
- **策略存档**：记录为什么改、怎么改、哪里失败
- **每日报告目录**：公开运行历史
- **交易所合约测试**：防止 API 对接层被策略改动误伤

---

## 风控层

| 层级 | 作用 |
|---|---|
| 仓位上限 | 防止单笔交易吃掉整个账户 |
| 杠杆上限 | 避免小幅反向波动直接变成爆仓风险 |
| 交易所端 SL / TP | bot 离线时仍由 Binance 自动平仓 |
| 代码端 SL / TP | bot 在线时的备份平仓逻辑 |
| 日亏损上限 | 糟糕的一天后停止开新仓 |
| 周亏损上限 | 防止连续多日复合亏损 |
| 连亏冷却 | 连续判断失败后强制暂停 |
| 手动熔断 | 创建 `data/KILLSWITCH` 立即阻止新开仓 |
| 状态对账 | 避免 bot 把过期本地状态当成真实持仓 |

更详细说明：[`docs/risk-control.md`](docs/risk-control.md)

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/jiangkaili/binance-trader-bot.git
cd binance-trader-bot

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入你自己的 Binance API key。
# 永远不要提交 .env 或任何真实凭据。

# 策略和风控参数在这里：
$EDITOR config/trader.yaml
```

### 3. 先安全运行

```bash
# Dry run：读取真实行情，但不下真实订单
python scripts/live_trader.py --dry-run
```

只有在你理解代码、API 权限和亏损边界后，才考虑 live 模式。

```bash
# Live mode：真实订单、真实资金、真实亏损可能发生
python scripts/live_trader.py
```

---

## 配置

核心策略和风控参数位于 [`config/trader.yaml`](config/trader.yaml)，保持扁平结构，方便审计：

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

环境变量放在 `.env`，不能提交：

| 变量 | 含义 |
|---|---|
| `BINANCE_API_KEY` | 带 Futures 权限的 Binance API key |
| `BINANCE_API_SECRET` | Binance API secret |
| `USE_TESTNET` | 是否使用 Binance testnet |
| `PROXY_HOST` / `PROXY_PORT` | 可选代理设置 |

---

## 项目地图

```text
binance-trader-bot/
├── README.md                  # 英文首页
├── README-Chinese.md          # 中文首页
├── ARCHITECTURE.md            # IO / 策略边界
├── STRATEGY_ARCHIVE.md        # 策略版本历史和决策记录
├── DISCLAIMER.md              # 金融和运行风险免责声明
├── SECURITY.md                # 凭据处理和漏洞报告
├── config/
│   └── trader.yaml            # 策略和风控参数
├── trader/
│   ├── exchange.py            # 冻结的 Binance IO 层
│   ├── trader.py              # 交易循环 / 策略层
│   ├── risk.py                # 风控规则
│   └── state.py               # 运行状态辅助
├── scripts/
│   ├── live_trader.py         # 生产入口
│   ├── list_algo_orders.py    # 检查交易所端保护单
│   └── place_safety_stop.py   # 必要时补挂保护单
├── reports/
│   └── README.md              # 每日报告索引
├── docs/
│   ├── index.html             # GitHub Pages 首页
│   └── risk-control.md        # 风控设计说明
└── tests/
    ├── test_exchange_contract.py
    └── test_trader_v2.py
```

`.env`、`data/`、日志和 SQLite 数据库都是运行时文件，默认不进入 git。

---

## 测试

```bash
pytest tests/test_exchange_contract.py -q
pytest tests/test_trader_v2.py -q
pytest tests/ -q
```

交易所合约测试尤其重要：它防止 SL / TP 被误改回错误的 API 端点。

---

## 贡献方向

比较适合的贡献：

- 更安全的风控规则
- 更清晰的日报 / postmortem 生成
- 交易所 API contract tests
- 如实包含亏损结果的策略研究
- 帮用户避免泄露密钥或不安全实盘的文档

不适合的 issue：保证盈利参数、带单信号、投资建议。

---

## 免责声明

本仓库仅用于教育和工程研究，不构成金融建议、投资建议或任何交易品种的推荐。数字资产合约和杠杆衍生品风险极高，可能造成本金全部损失。你需要自行负责密钥、交易、亏损、税务和合规义务。

---

## License

MIT License — see [`LICENSE`](LICENSE).

## Credits

- 原始网格交易框架来自 [51bitquant](https://github.com/51bitquant)
- 实盘执行、风控、报告和 Binance Algo Order 集成基于原框架扩展
- Binance API 行为参考官方 USDⓈ-M Futures 文档
