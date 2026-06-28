# AI Trading Lab

> 开源 AI 自动化交易实验：真实市场、真实风控、每日复盘、策略持续演化。

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20USD%E2%93%88--M-F0B90B?logo=binance)
![Focus](https://img.shields.io/badge/Focus-Risk%20Control-purple)
![Status](https://img.shields.io/badge/Experiment-Live%20Automation-red)

中文 | [English](README.md)

---

## 这是什么

一个公开的工程实验：自动化交易系统能不能在高噪声、高风险的加密货币合约市场里运行，同时不失控？

重点不是喊收益，而是：

- 交易所端风控，bot 崩了保护单还在
- 每日复盘报告，不是精选截图
- 策略版本记录和失败复盘
- 60 天回测驱动参数优化，不是拍脑袋
- 可复现的 SQLite 交易日志

> 风险提示：加密货币合约和杠杆可能导致本金全部损失。本项目仅用于工程研究和教育，不构成投资建议或信号服务。

---

## 回测驱动优化

所有策略参数变更都基于 60 天 BTCUSDT 5m 真实 K 线回放，含手续费和滑点近似。

### v4 → v5 策略演化

| 参数 | v4（旧） | v5（当前） | 为什么改 |
|---|---|---|---|
| RSI 超卖 | 20 | 12 | 20 在震荡市太松，假信号太多 |
| RSI 超买 | 80 | 88 | 只有真正极端才代表超买超卖 |
| 止损 | 0.6% | 0.5% | 稍微收窄，减少单笔最大亏损 |
| 止盈 | 0.9% | 1.0% | 放宽止盈，盈亏比从 1.5:1 到 2:1 |
| 交易后冷却 | 无 | 12 根 K线 (~1h) | 避免 RSI 极值区反复进场被止损 |

### 60 天回测对比

| 配置 | 交易数 | 胜率 | 总 PnL | 单笔期望 | 笔/天 |
|---|---|---|---|---|---|
| v4 (20/80, 0.6/0.9) | 219 | 42.5% | **-49.55 USDT** | -0.226 | 3.65 |
| v5 (12/88, 0.5/1.0, 冷却) | 69 | 52.2% | **+24.90 USDT** | +0.361 | 1.15 |
| 极端 (10/90, 1.0/1.5) | 34 | 58.8% | **+25.68 USDT** | +0.755 | 0.57 |

结论：BTC 近期是高波动震荡市，假极端信号多。少做、做极端、做完歇一会，比高频交易效果好。

---

## 风控架构

```text
行情数据
   │
   ▼
策略引擎 ──────────────────────┐
   │ RSI 指标                   │
   ▼                           │
风控管理器                      │
   │ 仓位上限                   │
   │ 日/周亏损上限               │
   │ 连亏冷却                   │
   │ 手动熔断 (KILLSWITCH)      │
   │ 交易后冷却                 │
   ▼                           │
执行层                         │
   │ Binance 市价单             │
   │ 交易所端止损/止盈           │
   ▼                           │
状态 + 日志                     │
   │ SQLite trades.db          │
   │ live_trader.state         │
   ▼                           │
每日报告 + 复盘 ◀───────────────┘
```

设计原则：策略层可以频繁迭代，但交易所 IO 层是承重墙，不能乱动。详见 [`架构说明.md`](架构说明.md)。

---

## 风控层级

| 层级 | 作用 |
|---|---|
| 仓位上限 | 单笔交易不超过账户的一定比例 |
| 杠杆上限 | 避免小幅反向波动直接爆仓 |
| 交易所端 SL/TP | bot 离线时仍由 Binance 自动平仓 |
| 代码端 SL/TP | bot 在线时的备份平仓逻辑 |
| 日亏损上限 | 糟糕的一天后停止开新仓 |
| 周亏损上限 | 防止连续多日复合亏损 |
| 连亏冷却 | 连续亏损后强制暂停 24h |
| 交易后冷却 | 每次平仓后等 12 根 K线再进场 |
| 手动熔断 | 创建 `data/KILLSWITCH` 立即阻止开仓 |
| 状态对账 | 不信任本地缓存，每 tick 从交易所拉真实持仓 |

详见 [`docs/risk-control.md`](docs/risk-control.md)

---

## 快速开始

### 方式 A：使用 [Hermes Agent](https://hermes-agent.nousresearch.com) 交互式安装

如果你使用 Hermes Agent，有一键交互式安装器：

```bash
# 将 skill 复制到 Hermes skills 目录
cp -r skill/deploy-binance-trader-bot ~/.hermes/skills/devops/

# 然后告诉 Hermes：「部署 binance-trader-bot」
# 它会引导你完成克隆、API 密钥配置、参数设置和验证
```

### 方式 B：手动安装

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
# 编辑 .env，填入你自己的 Binance API key
# 永远不要提交 .env

# 策略和风控参数：
$EDITOR config/trader.yaml
```

### 3. 先跑 Dry-run

```bash
# 读取真实行情，但不下真实订单
python scripts/live_trader.py --dry-run
```

理解代码、API 权限和亏损边界后再考虑 live：

```bash
# Live：真实订单、真实资金、真实亏损
python scripts/live_trader.py
```

---

## 当前配置 (v5)

```yaml
symbol: BTCUSDT
strategy_name: rsi_extremes_5m
rsi_period: 7
rsi_oversold: 12.0          # v5: was 20
rsi_overbought: 88.0        # v5: was 80
kline_interval: 5m
poll_seconds: 60
cooldown_bars_after_trade: 12  # v5 新增: ~1h 冷却
target_position_usdt: 25.0
leverage: 10
stop_loss_pct: 0.005        # v5: was 0.006
take_profit_pct: 0.010      # v5: was 0.009
daily_loss_pct: 0.25
weekly_loss_pct: 0.40
streak_cooldown_hours: 24
streak_loss_count: 3
```

---

## 核心特性

- Binance USDⓈ-M Futures 实盘执行引擎
- 交易所端止损/止盈（`/fapi/v1/algoOrder` 端点）
- 抗崩溃保护：bot/主机/网络挂了，保护单仍在交易所
- 配置驱动：所有参数集中在 `config/trader.yaml`
- SQLite 交易日志：可复现分析
- 多层风控：仓位/杠杆/日亏/周亏/连亏/冷却/熔断
- Dry-run 模式：真实行情模拟执行
- 策略存档：记录每次改动的原因和结果
- 交易所合约测试：防止 API 对接被误改

---

## 项目结构

```text
binance-trader-bot/
├── config/trader.yaml           # 策略与风控参数
├── trader/
│   ├── exchange.py              # Binance IO 层（冻结）
│   ├── config.py                # 配置 dataclass + YAML 加载
│   ├── models.py                # Position 数据结构
│   └── paths.py                 # 路径常量
├── gridtrader/quant/
│   ├── indicators.py            # 技术指标（RSI, EMA, ADX）
│   ├── strategies.py            # RSI 均值回归策略信号
│   ├── backtest.py              # 回测引擎
│   ├── hmac_client.py           # HMAC 签名请求工具
│   └── storage.py               # SQLite 交易记录
├── scripts/
│   ├── live_trader.py           # 生产入口
│   ├── sweep_multi.py           # 多策略回测扫描
│   ├── run_backtest.py          # 单次回测
│   ├── backtest_exit_logic.py   # 退出逻辑回测
│   ├── list_algo_orders.py      # 查看交易所端止损止盈
│   ├── place_safety_stop.py     # 紧急保护单
│   ├── check_open_orders.py     # 快速持仓检查
│   ├── trade_watchdog.py        # 监控运行中的机器人
│   ├── positions_futures.py     # 持仓查看
│   ├── transfer_to_futures.py   # 现货转合约
│   ├── fetch_klines.py          # K线数据获取
│   └── ping.py                  # API 连通性测试
├── tests/
│   ├── test_exchange_contract.py  # API 合约测试
│   ├── test_indicators.py         # 指标计算测试
│   └── test_strategies.py         # 策略信号测试
├── reports/                     # 每日复盘
├── docs/
│   ├── index.html               # GitHub Pages 首页
│   └── risk-control.md          # 风控设计说明
├── 架构说明.md                  # 架构说明
├── 策略归档.md                  # 策略版本历史
├── 安全策略.md                  # 安全策略
├── 贡献指南.md                  # 贡献指南
├── 路线图.md                    # 路线图
├── 视频教程.md                  # 视频教程
├── 使用手册.md                  # 使用手册
└── 免责声明.md                  # 免责声明
```

---

## 贡献方向

欢迎：

- 更安全的风控规则
- 更清晰的报告生成
- 交易所 API 合约测试
- 如实包含亏损结果的策略研究
- 帮助用户安全使用的文档

不欢迎：保证盈利参数、带单信号、投资建议。

---

## 免责声明

本仓库仅用于教育和工程研究。不构成金融建议、投资建议或交易推荐。加密货币合约风险极高，可能造成本金全部损失。

---

## Credits

- Binance API 行为遵循官方 USDⓈ-M Futures 文档
- 从零构建的自动化合约交易工程实验
