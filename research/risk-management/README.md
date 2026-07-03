# 风控知识库 / Risk Management Knowledge Base

> 持续生长的风控手册。每天研究学到的风控知识，按主题沉淀到对应章节（不是按日期堆放）。
> 服务对象：binance-trader-bot（BTCUSDT 永续，RSI 均值回归 + funding z-score，低杠杆小资金）。
> 用户要求：硬止损 + 每日/每周亏损上限必须落地。

## 怎么用这个库
- 每个主题一个文件，知识**累积进对应主题**，而非每天新开文件
- 每条知识标注：来源链接、对本 bot 的适用性、是否已在 bot 中实现
- 状态标签：`✅已实现` / `🧪实验中` / `📋待评估` / `❌不适用(附原因)`

## 主题章节
| 主题 | 文件 | 说明 |
|------|------|------|
| 止损技术 | `stop-loss.md` | 固定/移动/波动率止损、时间止损、ATR 止损 |
| 仓位管理 | `position-sizing.md` | 固定额、Kelly/分数Kelly、波动率调仓(vol targeting) |
| 回撤控制 | `drawdown-control.md` | 最大回撤限制、日/周亏损上限、连亏熔断 |
| 杠杆管理 | `leverage.md` | 杠杆与爆仓距离、动态杠杆、保证金缓冲 |
| 风险指标 | `risk-metrics.md` | 夏普/索提诺、最大回撤、盈亏比、期望、VaR |
| 市场状态 | `market-regime.md` | 趋势/震荡识别、波动率状态、HMM regime、vol targeting、相关性风险、避免不利环境交易 |
| 执行风险 | `execution-risk.md` | 滑点、手续费、资金费率成本、订单类型、延迟 |
| 极端风险 | `tail-risk.md` | 黑天鹅、闪崩、流动性枯竭、对冲与断路器 |

## 本 bot 当前已落地的风控（每次复盘时核对更新）
<!-- 与 config/trader.yaml 实际一致 -->
- 标的 BTCUSDT 永续，`target_position_usdt: 15.0` 保证金，`leverage: 5`（名义价值 75 USDT）
- 硬止损/止盈：`stop_loss_pct: 0.015` (1.5%) / `take_profit_pct: 0.030` (3.0%)，R:R=1:2，盈亏平衡胜率 33%。1.5%×5x=7.5%保证金≈1.1 USDT/笔亏损
- 信号退出已禁用(`disable_signal_exit: true`)：仓位只走 SL/TP/手动，不在反向 RSI 信号平仓
- 交易后冷却：`cooldown_bars_after_trade: 12`（12×5m≈1h 再入场），减少 RSI 反复触发的聚集入场
- 日/周亏损上限熔断：`daily_loss_pct: 0.25` / `weekly_loss_pct: 0.40`
- 趋势过滤：ADX>25 才允许新开仓(`trend_filter_adx_threshold: 25`)；EMA200 趋势对齐(仅 price>EMA200 做多、price<EMA200 做空)
- funding rate z-score 过滤：30 周期，|z|>2.0 确认 RSI 信号，|z|>3.0 可独立开仓
- 杠杆缓冲：5x（非10x）+ 15U 保证金给止损留呼吸空间，留手续费缓冲

## 待落地清单（研究中发现、值得做成实验的）
- **[2026-06-30] 实验 B：Block/Regime 蒙特卡洛回撤分布** 📋待评估 — 对 84 笔交易做 regime-conditioned block bootstrap(5000次)，输出最大回撤 5/50/95 分位 + 日亏25%/周亏40% 触达概率。把单条回测曲线变成「风控线真实触达概率」，直接服务硬性风控上限。⚠️ 必须用 block 不能用朴素 shuffle（bot 交易有 regime 聚集，朴素法低估尾部风险）。详见 `engineering/robustness.md` 第 2、5 节。
- **[2026-06-30] 实验 A：参数高原检查** 📋待评估 — RSI±2/SL·TP±0.25% 网格扫描，确认 20/80 是高原中心还是孤立尖峰（「唯一全正」本身是尖峰警报）。工程类，详见 `engineering/robustness.md`。
- **[2026-07-01] 实验 E1：下单幂等化** 📋待评估(优先级最高) — 下单前持久化 clientOrderId，超时改「先查询状态再决定重试」，绝不盲目重试。消灭「重复下单→双倍仓位→爆保证金」这个 15U 下的爆仓级 bug。详见 `execution-risk.md` 第 1 节 / `engineering/system-architecture.md`。
- **[2026-07-01] 实验 E2：启动对账** 📋待评估 — 每次启动先 REST 拉真实 position+openOrders，发现「裸仓(有仓无 SL/TP)」立即补挂止损或告警。根治「重启后孤儿仓位裸奔」，直接服务用户「硬止损必须落地」。详见 `execution-risk.md` 第 2 节。
- **[2026-07-02] 实验 D1：Closed-Bar Guard** 📋待评估 — 数据层断言 RSI/EMA/ADX 永远只使用已闭合 5m K线；REST 丢弃 closeTime 尚未越过安全延迟的最后一根，WS 只在 `k.x==true` 更新指标。排除「未闭合K线→伪信号→回测不可复现」的隐性前视偏差。详见 `engineering/data-engineering.md`。
- **[2026-07-02] 实验 D2：Funding As-Of Audit + 归因日志** 📋待评估 — 每次信号记录 funding 30条窗口的起止时间、最新 fundingTime、z-score、数据源，并断言 `fundingTime <= decision_time`；每笔成交记录滑点/手续费/funding fee，用于 live-vs-backtest reconciliation。详见 `engineering/live-vs-backtest.md` / `execution-risk.md`。
- **[2026-07-03] 实验 F1：Shadow 信号旁路** 📋待评估(工程主线) — 旁路进程读同样行情，用候选参数算信号只写日志不下单，与 champion 信号配对统计。零成本验证参数变体，是 champion-challenger 闭环的第一步（shadow 阶段）。把现有 bot+复盘+研究+KB 从「定时跑+人工复盘」升级为「自我验证闭环」。详见 `engineering/autonomous-iteration.md`。
- **[2026-07-03] 实验 F2：trader.yaml 版本化 + 一键回退** 📋待评估 — 每次参数变更存为一个 champion 版本(含回测摘要)，失败秒回退。是 canary 灰度的安全前提；也是 v8「唯一三窗口全正」这类尖峰参数的安全网。详见 `engineering/autonomous-iteration.md`。
- **[2026-07-03] 实验 G1：ATR high-vol 过滤** 📋待评估 — 5m ATR 处于近 N 根高位时跳过新开仓(均值回归在高波动易被打穿)。低成本增强现有 ADX/EMA 二值过滤，无需 ML。详见 `risk-management/market-regime.md`。
- **[2026-07-03] 实验 G2：vol-targeted 软调仓** 📋待评估 — 15U 基础按 `scale=target_ATR/current_ATR` 做 0.5x-1.5x 软调，高波动缩仓低波动略加。⚠️ 必须先回测 5m 调仓手续费(可能吃掉全部收益)；❌ 机械满版 vol targeting 不适用(顺周期性=跌完才减仓=卖低，详见 market-regime.md 第 3.3 节)。
