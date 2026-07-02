# 数据工程：时间边界、数据对齐与前视偏差防护

> 工程类知识沉淀（量化工程能力 60% 重心）。  
> 主题：行情、指标、funding、成交等数据在“当时是否已经可见”；用 Time Frontier / as-of join / closed-bar discipline 防止隐性前视偏差。  
> 服务对象：binance-trader-bot（BTCUSDT 永续，5m RSI 均值回归 + funding z-score）。

---

## 1. Time Frontier：数据到 EndTime 后才可用（2026-07-02）

### 是什么

QuantConnect 的 TimeSlices/Time Frontier 模型强调：回测引擎必须按现实中数据到达的顺序把数据交给算法；算法只能访问当前或过去的数据。数据点到达 `EndTime` 后才被发送给策略；intraday bars 通常在下一周期开始时才可用。

对本 bot 的翻译：**5m RSI/EMA/ADX 只能使用已闭合 5m K线，不能使用正在形成中的最后一根 K线。**

### Binance K线语义

- REST `/fapi/v1/klines`：K线由 `open time` 唯一标识，返回 OHLCV 与 `close time`。
- WebSocket `<symbol>@kline_<interval>`：当前 K线每 250ms 更新，字段 `x` 表示 `Is this kline closed?`。`x=false` 时 close/high/low/volume 都还会变。

### 本 bot 风险点

当前配置：`kline_interval: 5m`，`poll_seconds: 60`。若每分钟轮询时把 REST 返回的最后一根未闭合 K线纳入 RSI：

1. RSI7 会在同一根 5m 内反复变化，可能触发“收盘不存在”的 20/80 极值信号；
2. 回测只看到最终 OHLC，无法复现实盘瞬时信号，导致 live-vs-backtest 差异；
3. EMA200/ADX/funding confirm 若基于污染后的最后一根 bar，也会变成“半未来函数”；
4. 均值回归尤其容易被这种细节抬高胜率，因为极端 intra-bar 价格经常回收。

### 落地规则

- REST K线：只接收 `close_time < decision_time - safety_lag_ms` 的 bar；建议先用 1000–2000ms lag。
- WebSocket K线：只有 `k.x == true` 才更新指标与触发信号；`x=false` 仅作观察。
- 回测：第 N 根 bar close 后生成信号，最早在第 N+1 根 bar open 或下一可执行时点成交。
- 日志：每次决策记录 `decision_time`、`last_closed_kline_open_time`、`last_closed_kline_close_time`、`bar_age_ms`、是否丢弃未闭合 bar。

**状态：📋待评估 — 实验 D1：Closed-Bar Guard。** 只加数据层断言、日志和单元测试，不改策略逻辑；优先级高，成本低。

---

## 2. Funding as-of join：funding z-score 不能提前看未来（2026-07-02）

### 是什么

Funding rate 是永续合约的离散结算数据，不是 5m 连续指标。Binance `/fapi/v1/fundingRate` 返回 `fundingRate`、`fundingTime`、关联 `markPrice`，默认最近 200 条，按升序，limit 最大 1000；`/fapi/v1/premiumIndex` 返回 `lastFundingRate` 与 `nextFundingTime`；`/fapi/v1/fundingInfo` 可查某些合约的 funding interval 调整。

### 本 bot 风险点

当前配置：`funding_zscore_period: 30`，阈值 2.0，极值 3.0。若 BTC funding interval 为 8h，30 条约覆盖 10 天。问题在于：

- 回测若把 `fundingTime > decision_time` 的 funding rate 用进 z-score，就是前视偏差；
- 两个 funding settlement 之间，用“上一条已知 funding”与用 live `premiumIndex.lastFundingRate` 是不同语义，必须固定并记录；
- funding interval 可能因合约调整，不应硬编码 8h 到所有未来标的；
- 7 月底若扩到 tokenized stocks / 现货，美股代币没有 funding rate，filter 必须标记为 `❌不适用` 或替换成别的拥挤度/情绪指标。

### 落地规则

- 决策时刻 `t` 只能使用 `fundingTime <= t` 且现实中已发布的数据。
- 采用 as-of join：每根 5m bar 连接最近一条可见 funding 记录。
- 每次信号记录：`funding_latest_time`、30 条窗口的 start/end fundingTime、mean/std/z-score、数据源（history vs premiumIndex snapshot）。
- 回测审计：断言不存在 `funding_latest_time > decision_time`。

**状态：📋待评估 — 实验 D2：Funding As-Of Audit。** 不改信号逻辑，先让每次 funding confirm 可追溯。

---

## 3. 数据工程最小检查清单

| 检查 | 规则 | 状态 |
|---|---|---|
| K线闭合 | RSI/EMA/ADX 只吃闭合 bar | 📋待评估 |
| 时钟边界 | 用 safety lag 避免 closeTime 边界抖动 | 📋待评估 |
| funding as-of | fundingTime 不得晚于 decision_time | 📋待评估 |
| 指标 warmup | EMA200 至少 `warmup_bars: 210`，记录 warmup 是否充足 | ✅已实现（配置层） |
| 多源对齐 | K线、funding、订单事件统一 UTC ms 时间戳 | 📋待评估 |
| 原始数据留存 | 信号日志保存 used input snapshot，方便复盘重放 | 📋待评估 |

---

## 4. 自主迭代视角

“只能看当时已经可见的信息”不仅是量化回测原则，也是 autonomous agent 的通用约束：agent 每轮决策也必须区分 observation time、action time、result time。若事后把未来结果混进记忆/评估，就会出现交易里的 look-ahead bias 类似物——自我评估虚高、自动调参自欺。

---

## 来源

- QuantConnect TimeSlices / Time Frontier：https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/time-modeling/timeslices
- Binance USDⓈ-M Futures Kline REST：https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data
- Binance USDⓈ-M Futures Kline WebSocket：https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Kline-Candlestick-Streams
- Binance Funding Rate History：https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History
- Binance Mark Price / Premium Index：https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price
- Binance Funding Info：https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info
