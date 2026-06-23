# 🚀 Binance Futures Scalper

> 一个全自动的币安 USDⓈ-M 合约 RSI 均值回归交易机器人 — 自带交易所端止损保护、抗崩溃架构，已对接 2025 年新版 Algo Order API。

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Binance](https://img.shields.io/badge/Exchange-Binance%20Futures-F0B90B?logo=binance)
![Status](https://img.shields.io/badge/Status-Live%20Trading-red)

中文 | [English](README.md)

---

## 📊 实盘交易日报

> **这个机器人此刻正在币安合约上跑真金白银。** 每个交易日都会向本仓库提交一份最新的 markdown 日报，包含完整的交易记录、盈亏拆解、策略反思、以及当天修复的 bug。没有截图、没有精选的资金曲线 —— 只有 SQLite 数据库和 commit 历史在说话。

**📅 最新日报 → 见 [`reports/README.md`](reports/README.md) 顶部置顶**
**📁 全部日报 → [`reports/`](reports/)**

### 🔴 当前状态（每日自动更新）

| 指标 | 数值 |
|---|---|
| **运行状态** | 🟢 币安合约主网实盘运行中 |
| **交易对** | BTCUSDT @ 20× 杠杆，单笔目标 25 USDT |
| **策略** | 5 分钟 K 线上的 RSI(7) 极值均值回归 |
| **Bot 实现盈亏** | **+3.34 USDT**（4 笔已平仓交易，bot 主动平仓胜率 100%） |
| **首次成交** | 2026-06-21 |
| **最新动态** | 见 [`reports/`](reports/) 目录中最新的文件 |

> ⚠️ **完全披露**：SQLite 日志里还有 2 笔**手动补录**的交易所端止损成交（`order_id LIKE 'backfilled_%'`），是 bug 修复前发生的，合计 −8.25 USDT。这两笔没有计入上面的胜率统计，因为不属于 bot 自身的平仓决策。两笔记录依然保留在 `data/trades.db` 中，[`reports/`](reports/) 里的日报详细复盘了根因和修复过程。

### 📈 为什么要做每日日报？

GitHub 上大多数"交易机器人"项目展示一张回测曲线就草草了事。这个项目坚持**每个交易日提交一份带日期的 markdown 日报**，包括：

- ✅ **真实账户余额** —— 日报生成时从币安合约 API 实时查询
- ✅ **每一笔交易** —— 入场、出场、盈亏、平仓原因（信号 / 止损止盈 / 手动）
- ✅ **策略反思** —— 数据告诉我们什么有效、什么坏了、下一步参数怎么调
- ✅ **Bug 修复与代码变更** —— 当天提交的修复，附可复现的复现步骤
- ✅ **风控事件** —— 熔断触发、连亏冷却、保证金不足等

如果你想看一个 bot 在生产环境下**真实**的表现（而不是 Jupyter 里精心调出来的回测曲线），请翻一翻 [`reports/`](reports/) 里几天的日报。仓库里的交易数据库、日志文件、源代码都对得上 —— clone 下来用一样的 SQL 自己跑就能复现。

---

## ✨ 核心特性

- **全自动运行** —— 轮询币安合约、生成 RSI 信号、开仓平仓、挂止损止盈。无需人工干预。
- **抗崩溃保护** —— 止损止盈挂在**交易所**（不是只在代码里）。机器人挂了、Windows 崩了、断网了，币安照样会执行。
- **2025 新版 Algo Order API** —— 实现了币安新的 `/fapi/v1/algoOrder` 端点（2025 年 12 月起强制要求，大多数开源 bot 还在用已废弃的 `/fapi/v1/order` 然后报 `-4120` 错误）。
- **多层风控** —— 单笔止损止盈、日亏损上限、周亏损上限、3 连亏冷却、自动熔断。
- **多空双向** —— RSI 极值突破信号触发双向交易。
- **Dry-run 模式** —— 用真实行情数据纸面回测，不实际下单。
- **SQLite 交易日志** —— 每笔交易都记录，便于事后分析。
- **WSL 时钟漂移免疫** —— 每 30 分钟自动重新同步币安服务器时间（WSL 时钟漂得很快）。

## 📐 工作原理

```
┌─────────────────────────────────────────────────────────┐
│                    LIVE TRADER BOT                       │
│                                                         │
│  ┌──────────┐   ┌───────────────┐   ┌────────────────┐ │
│  │ 拉取 5m  │──▶│  RSI(7) 20/80 │──▶│  信号: BUY     │ │
│  │ K 线     │   │  均值回归     │   │  / SELL / FLAT │ │
│  └──────────┘   └───────────────┘   └───────┬────────┘ │
│                                              │          │
│                    ┌─────────────────────────▼───┐      │
│                    │      风控检查引擎             │      │
│                    │  • 日亏损上限                │      │
│                    │  • 周亏损上限                │      │
│                    │  • 3 连亏冷却                │      │
│                    │  • 自动熔断                  │      │
│                    └─────────────┬───────────────┘      │
│                                  │                      │
│              ┌───────────────────▼──────────────┐       │
│              │     币安合约 API                  │       │
│              │  POST /fapi/v1/order (MARKET)     │       │
│              │  POST /fapi/v1/algoOrder (SL/TP)  │       │
│              └──────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘

                    交易所端保护
         ┌──────────────────────────────────────┐
         │  STOP_MARKET     ← 价格触及 SL/TP    │
         │  TAKE_PROFIT_    │ 自动执行          │
         │  MARKET          │ （bot 死了也生效）│
         └──────────────────────────────────────┘
```

## 🏁 快速开始

### 1. 前置条件

- Python 3.10+
- 已开通合约交易的币安账户
- 拥有"合约交易"权限的 API key（在 [币安 API 管理](https://www.binance.com/en/my/settings/api-management) 创建）

### 2. 安装

```bash
git clone https://github.com/jiangkaili/binance-trader-bot.git
cd binance-trader-bot

python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env —— 填入你的币安 API key 和 secret

# 可选：调整策略参数
# 编辑 config/trader.yaml
```

### 4. 运行

```bash
# 纸面交易（不下真实订单，安全测试）：
python scripts/live_trader.py --dry-run

# 实盘交易（真金白银）：
python scripts/live_trader.py

# 或指定自定义 env 文件：
python scripts/live_trader.py --env-file .env.production
```

### 5. 停止

```bash
# 优雅关闭（先平仓再退出）：
kill -TERM $(cat data/trader.pid)

# 或在终端按 Ctrl+C
```

## ⚙️ 配置说明

### 环境变量（`.env`）

| 变量 | 说明 | 默认值 |
|---|---|---|
| `BINANCE_API_KEY` | 币安合约 API key | *必填* |
| `BINANCE_API_SECRET` | 币安合约 API secret | *必填* |
| `USE_TESTNET` | 是否使用币安测试网（纸面交易） | `false` |
| `PROXY_HOST` | 代理主机（如果币安被网络封锁） | *空* |
| `PROXY_PORT` | 代理端口 | `0` |

### 策略参数（`config/trader.yaml`）

所有交易参数都在一个 YAML 文件里。调参无需改代码。

```yaml
# 策略：5 分钟 K 线上的 RSI(7) 极值均值回归
symbol: BTCUSDT
kline_interval: 5m
poll_seconds: 60

# 仓位
target_position_usdt: 25.0   # 单笔保证金
leverage: 20                  # 杠杆倍数

# RSI 参数
rsi_period: 7
rsi_oversold: 20.0            # RSI 上穿此线时 BUY
rsi_overbought: 80.0          # RSI 下穿此线时 SELL

# 止损 / 止盈（价格变动百分比）
stop_loss_pct: 0.01           # -1% → 平仓
take_profit_pct: 0.01         # +1% → 平仓

# 风控上限（占起始权益的比例）
daily_loss_pct: 0.25          # 日亏损达 -25% 后停止交易
weekly_loss_pct: 0.40         # 周亏损达 -40% 后停止交易
```

## 📊 策略：RSI(7) 极值均值回归

**逻辑**：当 RSI(7) 跌至 20 以下（极度超卖）并反弹时做多；当 RSI(7) 突破 80（极度超买）并回落时做空。出现反向信号或触发 SL/TP 时平仓。

**为什么有效**：5 分钟 K 线上的 RSI 极值代表短期动能的衰竭。反弹是一个高概率反转机会 —— 但**只在极值区（20/80）**，不是大多数 RSI 机器人爱用的中性区（45/55）。

### 回测结果（5 天，BTCUSDT 5m，含手续费）

| 策略 | 交易笔数 | 胜率 | 净盈亏 |
|---|---|---|---|
| **RSI(7) 20/80** | 20 | **75%** | **+4.31%** |
| RSI(7) 25/75 | 33 | 63.6% | -2.44% |
| RSI(7) 30/70 | 42 | 57.1% | -3.29% |
| RSI(14) 45/55 | 76 | 51.3% | -3.25% |
| EMA9/21 交叉 | 68 | — | -5.80% |
| 布林带回归 | 42 | — | -6.10% |
| Donchian 突破 | 34 | — | -3.50% |

**核心洞察**：频率越低，胜率越高。20/80 极值过滤器排除了横盘震荡区的假信号。

## 🛡️ 风险管理

| 层级 | 触发条件 | 动作 |
|---|---|---|
| **交易所端 SL/TP** | 价格变动 ±1% | 币安自动平仓（不依赖 bot） |
| **代码端 SL/TP** | 价格变动 ±1% | bot 平仓（交易所端的备份） |
| **日亏损上限** | 日 P&L 达 -25% | bot 停止开新仓直到下一天 |
| **周亏损上限** | 周 P&L 达 -40% | bot 停止开新仓直到下一周 |
| **3 连亏冷却** | 连续 3 笔亏损 | 24 小时冷却期 |
| **自动熔断** | 累计亏损达起始权益的 -10% | 永久停止（需人工重置） |
| **手动熔断** | 创建 `data/KILLSWITCH` 文件 | bot 拒绝交易 |

## 🔌 API 实现

本 bot 使用币安**新版 Algo Order API**（2025 年 12 月起强制）。大多数开源 bot 还没迁移，会拿到 `-4120` 错误。

| 操作 | 端点 | 状态 |
|---|---|---|
| 创建条件单 | `POST /fapi/v1/algoOrder` | ✅ 已实现 |
| 撤销单笔 | `DELETE /fapi/v1/algoOrder` | ✅ 已实现 |
| 按 symbol 撤销所有 | `DELETE /fapi/v1/algoOpenOrders` | ✅ 已实现 |
| 查询挂单 | `GET /fapi/v1/openAlgoOrders` | ✅ 已实现 |

关键参数：`algoType=CONDITIONAL`、`triggerPrice`（不是 `stopPrice`）、`closePosition=true`。

## 📁 项目结构

```
binance-trader-bot/
├── .env.example              # 环境变量模板
├── config/
│   └── trader.yaml           # 策略与风控参数
├── scripts/
│   ├── live_trader.py        # ⭐ 主交易机器人
│   ├── positions_futures.py  # 查询持仓
│   ├── run_backtest.py       # 回测引擎入口
│   ├── sweep_all_15m.py      # 参数扫描
│   ├── fetch_klines.py       # 下载历史 K 线
│   ├── watchdog.sh           # 崩溃自动重启
│   └── ping.py               # API 连通性测试
├── gridtrader/               # Python 包（沿用上游框架的旧名字）
│   ├── quant/
│   │   ├── strategies.py     # 策略类（RSI、EMA、布林...）
│   │   ├── indicators.py     # 技术指标
│   │   ├── backtest.py       # 回测引擎
│   │   ├── risk.py           # 风控计算
│   │   ├── storage.py        # SQLite 交易存储
│   │   └── hmac_client.py    # 签名的币安 API 客户端
│   └── trader/               # GUI + gateway（上游遗留的网格模式）
├── tests/                    # Pytest 测试套件
├── data/                     # 运行时数据（trades.db、日志、状态）
├── reports/                  # 每日交易报告
└── requirements.txt
```

## 🖥️ 后台守护进程运行

### Linux / WSL

```bash
nohup python scripts/live_trader.py > data/stdout.log 2> data/stderr.log &
echo $! > data/trader.pid

# 配合 watchdog（崩溃自动重启）：
bash scripts/watchdog.sh &
```

### Windows (PowerShell)

```powershell
Start-Process -FilePath "python" -ArgumentList "scripts/live_trader.py" `
    -WorkingDirectory "C:\trader" -WindowStyle Minimized
```

## 📈 监控

bot 会写入几个文件方便监控：

| 文件 | 说明 |
|---|---|
| `data/live_trader.log` | 带时间戳的人类可读日志 |
| `data/live_trader.state` | 当前状态的 JSON 快照（持仓、P&L、信号） |
| `data/trades.db` | 所有交易和事件的 SQLite 数据库 |
| `data/pnl_state.json` | 持久化的日 / 周 P&L（重启后仍保留） |

快速检查：
```bash
# 当前状态
cat data/live_trader.state | python -m json.tool

# 最近的交易
sqlite3 data/trades.db "SELECT * FROM trades ORDER BY ts DESC LIMIT 10"

# 最近 20 行日志
tail -20 data/live_trader.log
```

## 🧪 测试

```bash
# 跑所有测试
pytest tests/ -v

# 带覆盖率
pytest tests/ --cov=gridtrader --cov-report=term-missing
```

## 📜 免责声明

**本软件仅供学习用途。** 加密货币合约带杠杆交易存在重大亏损风险。本 bot 可能让你血本无归。过往回测表现不代表未来收益。使用风险自负。永远不要拿你输不起的钱来交易。

## 📄 License

MIT License —— 详见 [LICENSE](LICENSE)。

## 🙏 致谢

- 原始网格交易框架来自 [51bitquant](https://github.com/51bitquant)
- RSI 策略与实盘交易引擎构建在 gridtrader 包之上
- Algo Order API 实现遵循 [币安官方文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Algo-Order)
