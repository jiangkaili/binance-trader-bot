# 交易系统工程：执行层可靠性 / System Architecture & Execution Reliability

> 工程类知识沉淀（量化工程能力 60% 重心）。
> 主题：让一个 7×24 无人值守的 bot「重试不重复、断线能恢复、重启能对账」——这是任何真实运行系统（交易 bot / AI agent / 后端）的通用可靠性骨架。
> 服务对象：binance-trader-bot（BTCUSDT 永续，RSI 均值回归 + funding z-score）。

---

## 1. 三大可靠性支柱（一句话记忆）

1. **幂等下单**：带副作用的操作（下单）出错时，「查询-再-决定」代替「盲目重试」；操作前先落地幂等键（clientOrderId）。
2. **断线重连**：连接一定会断。指数退避+jitter 重连 + 心跳/序列号检测丢消息 + 重连后 REST 兜底。永远别只信 WebSocket。
3. **状态对账**：交易所是唯一真相源。本地 vs 交易所不一致时无条件信交易所。启动时 + 定期 + 每次重连后都对账。

---

## 2. 幂等下单（Idempotent Order Placement）

### 网络请求的三种结局
1. 成功 2. **发出去且交易所执行了，但响应丢失/超时（最坑）** 3. 失败没到达。
朴素「失败即重试」在情况 2 造成**重复下单**（at-least-once + 副作用 = 可能重复执行）。

### Binance newClientOrderId 的坑 ⚠️
- 规则：`same newClientOrderId can be accepted only when the previous one is filled, otherwise rejected`。
- **只挡「上一单还挂着」时的重复。若上一单已成交（市价单几秒成交）→ 同 ID 新单被接受 → 照样重复。** 不是 exactly-once 银弹。
- clientOrderId 命名规则：`^[\.A-Z\:/a-z0-9_-]{1,36}$`。

### 正确姿势
1. 下单前**本地持久化 clientOrderId**，再发请求。
2. 超时/无响应 → 用该 ID 查订单（`GET /fapi/v1/order?origClientOrderId=`）：存在→采纳不重试；不存在→用同 ID 安全重试。
3. 成交以 `ORDER_TRADE_UPDATE`（user data stream）为准，不以下单 REST 响应为准。

**本 bot 契合度**：`target_position_usdt: 15.0` 小保证金下，重复下单=双倍仓位≈直接超保证金。幂等下单是爆仓级 bug 的直接解药。📋 待评估（实验 E1，优先级最高）。

---

## 3. 断线重连（WebSocket Reconnection）

### 三层防御（少一层 = 静默数据丢失）
```
L1 重连     → 把连接拉回来
L2 缺口检测 → 知道漏没漏消息（心跳 + 序列号）
L3 状态恢复 → 漏了用 REST 修
```

### L1 指数退避 + jitter
```
delay = min(base * 2^attempt, max)   # 1,2,4,8...s 封顶 30s
delay += delay * 0.2 * (rand*2-1)     # ±20% jitter 防惊群
```
按 WS close code 分流：1000/1001 立即重连；1008 先刷 auth；其余走退避。

### Binance 永续 user data stream 硬性事实（官方）
- `listenKey` 有效期 **60min**，须定期 `PUT` 续期；返回 `-1125` → `POST` 重建。
- 单连接**只活 24h**，到点必断——设计如此，**必须有自动重连**，不是 bug。
- 同连接同类型事件按 `T`(撮合时间)/`E`(事件时间)严格有序；跨类型比较用 `E`。
- 官方建议：波动行情 REST 有延迟，**稳态优先 WS 取订单/仓位**；重连后仍 REST 兜底（不矛盾）。

### L2 缺口检测
- 心跳：币安服务端发 ping、客户端回 pong；30s 无消息判「僵死连接」（连接在但不推消息，比断开更隐蔽）。
- 序列号：`收到 > 期望+1`→缺口，触发 REST 补齐；`< 期望`→重复/乱序，幂等丢弃。

**本 bot 契合度**：bot 用 `poll_seconds: 60` REST 轮询驱动，对「WS 漏消息」天然不敏感（优势）；但若成交确认也靠轮询/响应，缺实时成交回报。可选：加 user data stream 拿实时成交，或强化轮询对账（见第 4 节）。

---

## 4. 状态对账（Reconciliation）

### 信条
> 交易所是唯一真相源。本地 vs REST 不一致 → 无条件信 REST 改本地。

### 重启对账修 4 类漂移
1. **孤儿订单**：交易所有、本地无 → 收编 or 撤。
2. **幽灵订单**：本地有、交易所无（从没到达）→ 删本地。
3. **补录成交**：宕机期间成交 → 用 `/myTrades`/查订单补进本地账本。
4. **仓位核对**：拿交易所真实仓位终极校验 → **有没有裸仓（有仓无 SL/TP）？**

### 两个边界情况（Binance Academy）
- **撤单期间迟到成交**：撤单在飞时最后一笔成交可能到 → 以币安最终状态为准。
- **重连后乱序/重放**：每事件只处理一次（幂等），否则重复计数 → PnL 错、日亏熔断误触发。

### 落地要素
- 存：订单 ID、时间戳、原始/已成交/剩余数量、终态原因（FILLED/CANCELED/EXPIRED/REJECTED）。
- **status = 决策点，event = 解释**；幂等施加更新；**启动+定期+重连后**都对账。
- `REJECTED`（如保证金不足）永不进 open 状态，不可撤改——本 bot 15U 小保证金下须捕获，别当成「已挂单」。

**本 bot 契合度**：`poll_seconds: 60` 轮询天然带「周期对账」骨架，应显式强化。📋 实验 E2（启动对账，根治重启后裸仓）、E3（轮询里用交易所真实仓位覆盖本地）、E4（漂移事件结构化日志+告警喂复盘）。

---

## 5. 迁移价值（用户主线：学技术，不过时的硬技术）

「幂等键 + 唯一真相源 + 重连三层防御 + 对账循环」= 分布式系统可靠性通用范式：
- **扩美股代币（7 月底）**：现货同有下单超时/重启对账，直接复用。
- **AI agent / Claude-like 自主循环**：agent 调外部工具（下单/发消息/写文件）也是「带副作用的重试」——`clientOrderId` 就是 agent 的 idempotency key；「对账循环」就是 agent 每轮开始前 re-observe 真实环境。**这是自主系统的通用内核，不是交易专用。**

---

## 状态标签汇总
- 📋 E1 下单幂等化（clientOrderId + 查询再重试）——爆仓级 bug 解药，优先级最高
- 📋 E2 启动对账（openOrders+position+myTrades 三方核对，修裸仓/幽灵单）
- 📋 E3 60s 轮询升级为周期对账（交易所仓位覆盖本地）
- 📋 E4 漂移事件可观测性（结构化日志+告警→喂每日复盘，接自主迭代闭环）

## 来源
- Binance Dev Community: https://dev.binance.vision/t/how-is-exclude-duplicate-order-execution-by-api/1585
- Binance Academy《Understanding Order Status》: https://academy.binance.com/en/articles/binance-api-understanding-order-status
- Binance 官方 USDⓈ-M User Data Streams: https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams
- Binance 官方 New Order (TRADE): https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api
- MatrixTrak WebSocket Reconnection: https://matrixtrak.com/blog/websocket-disconnects-trading-bots-reconnection
- MatrixTrak Crash Recovery / Reconciliation: https://matrixtrak.com/errors/crash-recovery-double-orders-on-startup
