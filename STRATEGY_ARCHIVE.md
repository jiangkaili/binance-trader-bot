# 策略存档 — Strategy Archive

> 本文件记录所有跑过的策略版本、参数、实盘表现和被弃用的原因。
> **每次改策略前都要更新这里**：先写决策，再改代码，留下因果链。
>
> 当前生效版本：见末尾 "Current State" 段。
>
> 仓库：github.com/jiangkaili/binance-trader-bot
> 起始资金：45.83 USDT  (2026-06-18)
> 账户：Binance USDⓈ-M Futures 主网

---

## 时间线总览

| 版本 | 时间窗口 | 策略 | 杠杆 | SL/TP | 关键变量 | 实盘 P&L | 状态 |
|------|---------|------|------|-------|---------|---------|------|
| v0   | 2026-06-18 (paper only) | RSI extreme revert | 20x | 1% / 1% | thresh 30/70, signal-exit ON | dry-run | 弃用 |
| v1   | 2026-06-21 ~ 06-22 | RSI dynamic thresholds | 20x | 1% / 1% | thresh 多档动态, signal-exit ON | ≈+1.61 USDT (LIVE, 3 trades) | 弃用：回测 -40 USDT/14d |
| v2   | 2026-06-22 ~ 06-23 02:00 | RSI extreme revert v2 | 5x | 0.5% / 1.0% | thresh 20/80, signal-exit OFF | -5.97 USDT (LIVE) | 弃用：SL 太紧 |
| v3   | 2026-06-23 02:00 ~ 11:00 | RSI extreme revert v3 | 5x | 1.0% / 1.5% | thresh 20/80, signal-exit OFF | -9.34 USDT (LIVE, 2 SL stop-outs) | **暂停中** (KILLSWITCH 06-23 23:32) |
| v4   | 待启用 | RSI + ADX 趋势过滤 | 3x | 2% / 3% | thresh 20/80, ADX<25 才开仓 | — | **草案** |

---

## v0 — RSI Extreme Mean Reversion（初代）

**时间**：2026-06-18（仅纸面 / 学习阶段，未上 LIVE）
**Commit**：`624beaa feat: open-source ready — config separation, README, exchange-side SL/TP`

**核心思想**：均值回归。RSI 极端值时假设市场过度反应，反向开仓等回归均值。

**参数**：
```yaml
strategy_name: rsi_extremes_5m
rsi_period: 7              # 7-period RSI on 5m bars
rsi_oversold: 30.0
rsi_overbought: 70.0
target_position_usdt: 25
leverage: 20               # 25 * 20 = 500 USDT 名义
stop_loss_pct: 0.01        # 1% 价格变动止损
take_profit_pct: 0.01      # 1% 价格变动止盈
disable_signal_exit: false # 反向信号会平仓
```

**信号逻辑**：
- RSI < 30 → BUY (做多)
- RSI > 70 → SELL (做空)
- 反向信号 → 平仓（signal-exit）

**问题**：
1. **20x 杠杆 + maker/taker fee** = 一个来回 ≈1.6% 保证金；高频开平直接被手续费吃掉
2. **signal-exit 在 1% TP 前就关赢家**：实测出场均在 +0.2%，TP 几乎从未触发
3. 回测 14 天 → -40 USDT，期望 -2.86 USDT/天

**Live 表现**：未实战，回测后直接淘汰。

---

## v1 — RSI Dynamic Thresholds（动态阈值）

**时间**：2026-06-21 15:42 ~ 2026-06-22 00:00 ish
**Commit**：(参数级调整，未单独 commit，体现在 `624beaa` 之后的运行配置中)

**变化 vs v0**：用了多套阈值，根据 5m 波动率自适应。从事件日志看实际跑过的阈值：

```
2026-06-21 15:42  SHORT @ RSI > 55.0      ← 阈值缓和
2026-06-21 16:00  LONG  @ RSI < 45.0
2026-06-21 16:22  SHORT @ RSI > 53.0
2026-06-21 20:42  LONG  @ RSI < 20.0      ← 切回极端
```

**实盘成绩** (3 笔已知 P&L)：
```
2026-06-21 15:59  CLOSE SHORT  +0.20  ✓
2026-06-21 16:22  CLOSE LONG   +0.13  ✓
2026-06-21 20:41  CLOSE SHORT  +1.28  ✓
                  合计       +1.61 USDT
```

**问题**：阈值经常乱跳，缺少回测支撑；3 战 3 胜可能只是运气好（样本太小）。

**弃用原因**：用户决定回归 v0 思想但修复 fee/signal-exit 问题 → v2。

---

## v2 — RSI Extreme + 5x Lev + Tight SL

**时间**：2026-06-22 00:38 ~ 2026-06-23 02:00 ish
**Commit**：参数调整，未单独 commit；体现在 `42c7070` 期间的 `config/trader.yaml`

**变化 vs v0**：
1. **杠杆 20x → 5x**：手续费占保证金从 1.6% 降到 0.4%
2. **rsi_oversold 30→20 / overbought 70→80**：只在最极端时开仓
3. **disable_signal_exit: true**：让赢家跑到 TP，不再被反向信号过早平
4. **stop_loss_pct 1.0% → 0.5%**：理论上盈亏比变 2:1，breakeven WR 34%

**参数**：
```yaml
leverage: 5
rsi_oversold: 20.0
rsi_overbought: 80.0
stop_loss_pct: 0.005       # 0.5% 价格变动
take_profit_pct: 0.01      # 1.0% 价格变动
disable_signal_exit: true
```

**信号阈值时间线**：
```
2026-06-22 00:38  SHORT @ RSI > 80.0    ← v2 上线
2026-06-22 03:01  LONG  @ RSI < 20.0
2026-06-22 07:31  SHORT @ RSI > 80.0
2026-06-22 16:08  LONG  @ RSI < 20.0
```

**实盘成绩**：
```
2026-06-21 23:31  BACKFILL  -2.27 USDT   ← v1→v2 切换造成的跨版本平仓
2026-06-22 22:28  BACKFILL  -5.97 USDT   ← 单笔最大亏损
                  合计     -8.24 USDT
```

**问题**：
- BTCUSDT 5m bar 实际波动 0.5%~1.0%，**0.5% SL 总是被噪声打掉**
- 06-22 那笔 -5.97 是典型例子：方向看对了，但提前被打止损甩飞

**弃用原因**：SL 太紧，反复无故停损。

---

## v3 — RSI Extreme + 5x Lev + 1.5:1 R/R（当前已暂停）

**时间**：2026-06-23 02:00 启用 → 11:00 进入 KILLSWITCH
**Commit**：`d7a8c2e risk overhaul v3: 5x leverage + disable signal-exit + 1.5:1 reward/risk`

**变化 vs v2**：
- `stop_loss_pct: 0.005 → 0.01`（SL 加宽到 1%，给 BTC 噪声空间）
- `take_profit_pct: 0.01 → 0.015`（TP 提到 1.5%，盈亏比从 2:1 调成 1.5:1）
- 计算：5x 杠杆下 SL=-5% margin (-1.25 USDT)，TP=+7.5% margin (+1.88 USDT)
- breakeven WR = 40%

**参数**（当前 `config/trader.yaml`）：
```yaml
symbol: BTCUSDT
target_position_usdt: 25
leverage: 5
rsi_period: 7
rsi_oversold: 20.0
rsi_overbought: 80.0
stop_loss_pct: 0.01
take_profit_pct: 0.015
disable_signal_exit: true
daily_loss_pct: 0.25       # 日亏损 25% 触发硬停
weekly_loss_pct: 0.40
streak_cooldown_hours: 24  # 连亏 3 笔进入 24h 冷却
streak_loss_count: 3
```

**实盘成绩** (LIVE)：
```
2026-06-23 01:09  CLOSE LONG  signal_sell  +1.73  ✓  (v2→v3 过渡期那笔，赢家终于跑到 TP)
2026-06-23 06:21  EXT CLOSE LONG  exchange_sl_tp  -5.14  ✗  (-1% SL 触发)
2026-06-23 08:26  CLOSE LONG  stop_loss   -4.20  ✗  (-0.84% bot 端 SL 触发，比 exchange 算法快)
2026-06-23 11:21  OPEN SHORT @ 62495.2  (当前持仓，浮盈 +0.33 USDT)
                  合计       -7.61 USDT (含当前未平仓浮盈)
```

**核心问题（数据驱动结论）**：

| 维度 | 数据 | 解读 |
|------|------|------|
| 胜率 | 4/8 = 50% | 信号本身不算差 |
| 平均盈 | +0.93 USDT | TP 很少触发，多靠 signal-exit 出场 |
| 平均亏 | -4.40 USDT | SL 触发即满额损失 |
| 盈亏比 | 1 : 4.7 | **数学注定亏损** |
| 期望/笔 | -1.74 USDT | 50% WR × 0.93 - 50% WR × 4.40 |
| 方向分布 | LONG 6 笔 / SHORT 2 笔 | 因为 BTC 单边下跌，做多全是抄半山腰 |
| LONG 战绩 | 2 胜 4 负 / -15.72 USDT | RSI 均值回归遇到趋势 = 反复挨刀 |
| SHORT 战绩 | 2 胜 0 负 / +1.48 USDT | 顺势的方向是赢的 |

**根本病因**：RSI 均值回归策略假设"市场会回归"，但 BTC 06-22 后进入单边下跌行情，每次 RSI 超卖都不是底而是中继。

**当前状态**：`KILLSWITCH` 已置位（2026-06-23 23:32 UTC+8），SHORT 0.002 继续持有等 SL/TP/手动平，**不再开新仓**。

---

## v4 — RSI + ADX 趋势过滤（草案，未启用）

**目标**：在 v3 基础上加趋势过滤，只在震荡市做均值回归。

**计划变化**：
1. **加 ADX(14) 过滤**：只在 `ADX < 25` 时开仓（震荡市判定）
2. **杠杆 5x → 3x**：单笔最大损失从 -1.25 降到 -0.75 USDT，更耐错
3. **SL 1% → 2%**：进一步放宽，避免 BTC 5m 噪声止损
4. **TP 1.5% → 3%**：维持 1.5:1 盈亏比
5. **保留**：disable_signal_exit、daily/weekly cap、streak cooldown

**预期参数**：
```yaml
leverage: 3
stop_loss_pct: 0.02
take_profit_pct: 0.03
adx_period: 14
adx_max_for_entry: 25.0
```

**预期数学**（3x 杠杆下）：
- SL = -6% margin = -1.50 USDT
- TP = +9% margin = +2.25 USDT
- breakeven WR = 40%
- 单笔风险 1.50 USDT vs 账户 40 USDT = 3.75% per trade（更健康）

**风险**：
- ADX 过滤会大幅减少交易频率（可能每天 0-2 单），样本不足验证胜率
- 趋势市完全不交易 → 错过 SHORT 顺势的机会
- 需要先回测验证 ADX<25 在 BTC 5m 上的有效性

**状态**：**未启用**。代码改造和回测都没做。等当前 SHORT 平仓后评估。

---

## 备选方向（未决定）

### 选项 A：v4 RSI + ADX 过滤（保留均值回归思路）
- 优点：现有代码改动小，风险可控
- 缺点：BTC 长期是 trending asset，均值回归本质上逆势

### 选项 B：彻底换策略 — 趋势跟随
- 比如 Donchian breakout / EMA crossover / Supertrend
- 优点：与 BTC 当前行情匹配
- 缺点：重写策略 + 重做回测，工作量大

### 选项 C：先躺平观察，停一周
- 优点：保留 40 USDT 本金不再亏
- 缺点：错过可能的修复窗口，但反正现在是亏的

---

## Current State (2026-06-23 23:32 UTC+8)

```
账户余额            : 39.99 USDT  (起始 45.83，累计 -5.84)
启动资金 (本周)     : 40.05 USDT  (本周风控基准)
当前持仓            : BTCUSDT SHORT 0.002 @ 62495.2  (5x)
                     SL 63120.2 / TP 61557.8
                     浮盈 +0.33 USDT
当前生效版本        : v3 (config/trader.yaml)
KILLSWITCH          : 已置位 (data/KILLSWITCH)
                     → 当前 SHORT 继续，不开新仓
INTCUSDT 孤单       : 142.5 SL 仍挂着（未清，背景账上）
机器人进程          : Windows 上仍跑 (scripts/live_trader.py)
v2 重构包           : trader/ (已 push，未启用)

下一步             : 等 SHORT 平仓 → 评估 v4 / 选项 B / 选项 C
```

---

## 修订日志

| 日期 | 修订 | 说明 |
|------|------|------|
| 2026-06-23 | 创建本文件 | 整理 v0→v3 历史，记录 v3 暂停决策 |
