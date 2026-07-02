# 实盘 vs 回测归因 / Live vs. Backtest Reconciliation

> 工程类知识沉淀（量化工程能力 60% 重心）。  
> 主题：实盘与回测的偏差来源、系统性归因方法、如何让复盘从“描述亏损”升级为“定位差异”。  
> 服务对象：binance-trader-bot（BTCUSDT 永续，5m RSI 均值回归 + funding z-score）。

---

## 1. 核心思想

QuantConnect 的 Live Reconciliation 模型：同时段跑实盘 + OOS 回测，两条权益曲线叠在一起。如果完全重合，说明建模准确；偏差越大，越说明模型在某个环节和现实不符。

> 每一笔实盘偏差都应该能归因：是信号不同？成交价不同？手续费/资金费率没建模？风控触发差异？订单状态/部分成交？

---

## 2. 本 bot 的偏差来源清单（2026-07-02）

| 偏差来源 | 影响 | 归因方法 |
|---------|------|---------|
| 时间边界差异 | 实盘用了未闭合 K线；回测只看到最终 bar | 记录 `last_closed_kline_close_time`、`bar_age_ms` |
| 成交时点 | 回测假设下根 bar open/close 成交；实盘有提交延迟、撮合延迟、拒单、滑点 | 记录 `submit_time`、`ack_time`、`first_fill_time`、`expected_price_model`、`avg_fill_price`、`slippage_bps` |
| 部分成交 | 小额订单也可能部分成交，导致持仓成本和预期不同 | 记录 `final_status`（FILLED/PARTIALLY_FILLED/CANCELED/REJECTED） |
| 手续费 | 回测可能用标准费率，实盘用 BNB 折扣或 VIP 等级 | 记录每次 `commission_usdt` |
| funding 成本 | 回测可能没建模或建模方式不同 | 每次 `funding_fee_usdt_since_entry`，与 `funding_latest_time` 对齐 |
| 风控触发 | SL/TP 回测按 OHLC 触达，实盘有触发价/延迟/部分成交 | 记录实际 `stop_loss_price`、`take_profit_price` 触发时间与价格 |
| 重连/重启 | 孤儿仓位、重复下单、重复计数、漂移事件 | 见 `execution-risk.md` 与 `system-architecture.md` |

---

## 3. 归因日志设计（2026-07-02）

每次信号/下单/成交记录结构化字段（见当日流水笔记中的 schema 示例）。每日/每周 reconciliation 表：

| 维度 | 计算方式 | 用途 |
|---|---|---|
| 信号一致率 | 实盘信号 vs 同期回放信号 | 定位时间边界/数据差异 |
| 成交滑点 | 实盘均价 - 回测假设价 | 校正 fill model |
| 手续费差 | 实盘 commission - 回测 commission | 修手续费模型 |
| funding 差 | 实盘 funding fee - 回测 funding fee | 修资金费率模型 |
| 风控差 | SL/TP 触发时刻与价格差 | 修止损触发模型 |

---

## 4. 订单生命周期状态机（参考 Hummingbot 架构，2026-07-02）

Hummingbot 的订单生命周期将订单视为状态机：

1. **创建**：`buy()`/`sell()` 返回 client-side order ID，调度提交
2. **跟踪**：`start_tracking_order()` 在 API 请求前调用
3. **提交**：`_create_order()` 做交易规则检查后提交 REST，成功后发 `BuyOrderCreatedEvent` 或 `SellOrderCreatedEvent`，失败发 `MarketOrderFailureEvent`
4. **填充**：每笔（部分或完全）成交发 `OrderFilledEvent`
5. **完成**：完全成交后发 `BuyOrderCompletedEvent`/`SellOrderCompletedEvent`
6. **取消/过期**：`OrderCancelledEvent`/`OrderExpiredEvent`
7. **失败**：`MarketOrderFailureEvent`

本 bot 当前用 `poll_seconds: 60` 轮询模式，即使不改为 WS 状态机，也应确保：
- 下单返回 ≠ 订单完成；需跟踪订单状态直到终态
- `REJECTED` 必须捕获并告警，不要当成“已挂单”
- 部分成交需要记录，影响持仓均价与风控

---

## 5. 自主迭代视角

Reconciliation 是 Maker-Checker 模式的核心 Checker：Maker 生成策略/参数预期，Checker 用实盘-回测差异表告诉我们系统是不是在自欺。这正是用户感兴趣的“小型自主迭代系统”的工程内核。

---

## 来源

- QuantConnect Live Trading Reconciliation：https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation
- QuantConnect OOS Reconciliation（Research Environment）：https://www.quantconnect.com/docs/v2/research-environment/meta-analysis/live-reconciliation
- Hummingbot Order Lifecycle and Market Events：https://hummingbot.org/connectors/connectors/architecture/order_lifecycle/
- Binance Academy Understanding Order Status：https://academy.binance.com/en/articles/binance-api-understanding-order-status