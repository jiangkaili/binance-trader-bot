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
| 市场状态 | `market-regime.md` | 趋势/震荡识别、波动率状态、避免不利环境交易 |
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
