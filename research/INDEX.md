# 量化策略与风控研究知识库 / Quant Strategy & Risk Research

> 每日 21:00 自动更新。聚焦：加密永续合约、均值回归、funding rate、低杠杆小资金风控。
> 目标：为 binance-trader-bot（BTCUSDT 永续，RSI 均值回归 + funding z-score）做知识储备。

## 选题原则
- 优先研究**能移植到本 bot** 的策略/风控技术，而非泛泛的教科书内容
- 每天 1-2 个主题，避免与已有条目重复（先读本索引）
- 每个主题必须给出"对本 bot 的可操作启发"

## 已研究主题索引
<!-- 格式：| 日期 | 主题 | 类别 | 可移植性 | 笔记文件 | -->
| 日期 | 主题 | 类别 | 可移植性 | 笔记 |
|------|------|------|----------|------|
| 2026-06-30 | Walk-Forward 分析 + 蒙特卡洛重采样 + 参数高原 | 工程(回测验证) | 高 | [`2026/06/2026-06-30-walk-forward-and-monte-carlo-validation.md`](2026/06/2026-06-30-walk-forward-and-monte-carlo-validation.md) · 沉淀 [`engineering/robustness.md`](engineering/robustness.md) |
| 2026-07-01 | 执行层可靠性：幂等下单 + 断线重连 + 状态对账 | 工程(交易系统工程/可观测性) | 极高 | [`2026/07/2026-07-01-idempotent-orders-reconnect-reconciliation.md`](2026/07/2026-07-01-idempotent-orders-reconnect-reconciliation.md) · 沉淀 [`engineering/system-architecture.md`](engineering/system-architecture.md) |

## 类别标签
- `策略`：入场/出场信号逻辑
- `风控`：止损、仓位管理、回撤控制、资金管理
- `指标`：技术/链上/衍生品指标
- `执行`：滑点、手续费、订单类型、延迟
- `回测`：验证方法论、过拟合防范
