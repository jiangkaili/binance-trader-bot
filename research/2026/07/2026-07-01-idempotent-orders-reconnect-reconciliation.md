# 2026-07-01 执行层可靠性工程：幂等下单 + 断线重连 + 状态对账

> 类别：**工程（交易系统工程 / 可观测性 / 自主迭代内核）** — 属研究重心的 60% 工程能力块
> 可移植性：**极高**。这套「重试不重复、断线能恢复、重启能对账」是任何真实运行系统（交易 bot / AI agent / 后端服务）的通用可靠性骨架，换市场、换策略、换到 Claude-like agent 循环都成立。
> 服务对象：binance-trader-bot（BTCUSDT 永续，RSI 均值回归 + funding z-score，7×24 无人值守运行）。

---

## 为什么今天研究这个

前几天研究的是「回测能不能信」（walk-forward / 蒙特卡洛）。但一个真实运行的 bot，**回测再稳，执行层崩了照样亏钱**——而且亏的是那种「不该发生」的钱：

- 下单请求超时 → 以为没成交 → 重试 → **实际两单都成交了 = 双倍仓位**（这是本 bot 15U 小保证金下的致命伤，双仓直接超保证金）
- 进程崩溃/WSL 重启/网络断 → 重启后 bot 内存里「没仓位」，但交易所里**真有一个开着的仓位在裸奔**（孤儿仓位 orphan position），没有 SL/TP 保护
- WebSocket 断线期间成交了一笔 → bot 没收到成交回报 → 内存状态和交易所真相**永久漂移**

用户的主线是「学真实的下单/滑点/止损/API/断线」——这一篇正是那个「断线/API」的核心工程课，也是「睡后代码自主循环」能不能真正无人值守的前提。

---

## 一、幂等下单（Idempotent Order Placement）——重试不重复

### 核心问题
网络请求有三种结局，第二种最坑：
1. 成功（收到响应）
2. **发出去了、交易所也执行了，但响应丢了 / 超时**（你以为失败，其实成功）
3. 失败（交易所没收到）

朴素的「失败就重试」在情况 2 会造成**重复下单**。这是分布式系统的经典难题：at-least-once 投递 + 副作用 = 可能重复执行。

### Binance 的机制与它的坑（重要，别踩）
- 下单参数 `newClientOrderId`：可自定义唯一 ID（`^[\.A-Z\:/a-z0-9_-]{1,36}$`）。
- 官方规则原文：**"same newClientOrderId can be accepted only when the previous one is filled, otherwise the order will be rejected."**
- ⚠️ **致命陷阱**：这条规则只在「上一单还挂着（未成交）」时挡住重复。**如果上一单已经成交（尤其市价单几秒就成交），交易所会接受同 ID 的新单 → 照样重复！** 币安开发者社区里就是有人 market order 被执行了两次。
- 所以 `newClientOrderId` **不是** exactly-once 的银弹，只是「防挂单重复」的第一道弱防线。

### 正确姿势（社区/官方共识）
> "Always rely on user data stream to confirm the status of the order you sent out. That's the most effective way to avoid duplicate orders." — Binance Dev Community (MJW)

1. 下单前，**本地先生成并持久化 `clientOrderId`**（写入本地状态/DB），再发请求。
2. 请求超时/无响应时，**不要盲目重试**。先用这个 `clientOrderId` 去 **查询订单状态**（REST `GET /fapi/v1/order?origClientOrderId=...`）：
   - 已存在 → 说明第一单成功了，**不重试**，直接采纳交易所返回。
   - 不存在 → 才安全重试（用同一个 clientOrderId）。
3. 成交确认**以 user data stream 的 `ORDER_TRADE_UPDATE` 为准**，不以下单 REST 响应为准。

一句话原则：**「查询-再-决定」代替「盲目重试」；副作用操作前先落地幂等键。**

---

## 二、断线重连（WebSocket Reconnection）——三层防御

来源：MatrixTrak《WebSocket Reconnection That Actually Works》(2026-02)。核心断言：**WebSocket 一定会断**（交易所每 24h 强制断连、网络抖动、负载均衡轮转、代理超时），问题不是「会不会断」而是「断了能不能正确恢复」。

> 只做三件事也要做：① 指数退避+jitter 自动重连 ② 用序列号检测丢消息 ③ 重连后一定用 REST 核对状态，**永远别只信 WebSocket**。

### 三层防御模型
```
Layer 1: 重连 (Reconnection)   │ 把连接拉回来
Layer 2: 缺口检测 (Gap Detect) │ 知道自己漏没漏消息
Layer 3: 状态恢复 (State Recovery) │ 漏了就用 REST 修
```
少任何一层 → 「静默数据丢失」（silent data loss），比崩溃更可怕，因为你不知道自己错了。

### Layer 1：指数退避 + jitter
```
delay = min(base * 2^attempt, max)        # 1s,2s,4s,8s... 封顶 30s
delay += delay * 0.2 * (rand*2-1)          # ±20% jitter，防「惊群/thundering herd」
```
- jitter 关键：如果所有客户端同时断（交易所维护），无 jitter 会导致所有 bot 同一刻重连把服务打爆。
- 按 close code 区别对待：1000/1001（正常/服务重启）立即重连；1008（策略违规）先刷新 auth；其余走退避。

### Binance 永续 user data stream 的硬性事实（官方文档）
- `listenKey` 有效期 **60 分钟**；必须定期 `PUT` 续期。若返回 `-1125 "listenKey does not exist"` → 用 `POST /fapi/v1/listenKey` **重建**。
- 单条连接**只活 24 小时**，到点必被断——**这是设计如此，必须有自动重连**，不是 bug。
- 消息排序：同一连接、同类型事件（`ORDER_TRADE_UPDATE`/`ACCOUNT_UPDATE`）按 `T`（撮合时间）和 `E`（事件时间）**严格有序**；跨类型比较推荐用 `E` 排序。
- 官方建议：波动行情下 REST 可能有查询延迟，**优先用 WebSocket user data stream 获取订单/仓位**（但重连后仍要 REST 兜底对账——两者不矛盾：稳态信 WS，恢复期信 REST）。

### Layer 2：缺口检测（心跳 + 序列号）
- 心跳/ping-pong：默认每 15s ping，30s 无响应判定连接「僵死」（连接还在但不推消息了，比断开更隐蔽）。币安是服务端发 ping、客户端回 pong。
- 序列号：消息带序号时，`收到序号 > 期望+1` → 有缺口，触发 REST 补齐；`< 期望` → 重复/乱序，丢弃（幂等处理）。

---

## 三、状态对账（Reconciliation）——重启/断线后修正「内存 vs 交易所真相」

来源：MatrixTrak《Crash Recovery》+ Binance Academy《Understanding Order Status》。

### 核心信条
> **交易所是唯一真相源（single source of truth）。本地状态和交易所不一致时，无条件信交易所，改本地。**
> "If the REST API result differs from the local state, trust the REST result and update the local state." — Binance Academy

### 重启对账循环要修的 4 类漂移
1. **孤儿订单（orphan orders）**：交易所上有、本地没有的挂单 → 决定「收编（adopt）还是撤掉」。
2. **幽灵订单（ghost orders）**：本地以为存在、实际从没到达交易所 → 从本地删除。
3. **补录成交（backfill stale fills）**：宕机期间发生的成交 → 用 REST `/myTrades` / 查订单补进本地账本。
4. **仓位核对（verify position）**：最后拿交易所真实仓位做终极校验（对本 bot：**有没有一个没被 SL/TP 保护的裸仓？**）。

### 两个必须处理的边界情况（Binance Academy 原文）
- **撤单期间的迟到成交（late fills during cancellation）**：撤单还在飞的时候，最后一笔成交可能到达。**以币安发的最终状态为准。**
- **重连后消息乱序（out-of-order on reconnect）**：重连可能重放/乱序推送。**每个事件只处理一次（幂等）**，避免重复计数（比如把一笔成交算成两笔，PnL 就错了，日亏熔断也会误触发）。

### 落地要素（Binance Academy Best Practices）
- 存：订单 ID、时间戳、原始/已成交/剩余数量、终态原因（FILLED/CANCELED/EXPIRED/REJECTED）——足够做对账、报表、防重复计数。
- **把订单状态当「决策点」，把 stream 事件当「解释」**（status = 决定做什么，event = 解释发生了什么）。
- **幂等地施加更新**，防重连重复计数。
- **定期 + 每次重连后**都跑一次对账，不只在启动时。

### Binance 永续订单状态机（对本 bot 的最小集）
`NEW → PARTIALLY_FILLED → FILLED`（成功链）；旁路终态 `CANCELED / EXPIRED / REJECTED`。
- `EXPIRED`：GTC/IOC/FOK 条件不满足会出现。
- `REJECTED`：交易所直接拒（比如保证金不足、参数错）——**永远不会进 open 状态，不能撤/改**。本 bot 15U 小保证金下，保证金不足被 REJECTED 是真实风险，必须捕获而不是当成「已挂单」。

---

## 四、对本 bot / 对工程能力的启发（最关键）

### 这套东西本 bot 现状（据 config 与复盘推测，需用户核对代码）
- bot 用 `poll_seconds: 60` **REST 轮询** K 线驱动 → 好处：不强依赖 WS，天然对「WS 断线漏消息」不敏感；坏处：**成交确认、仓位状态很可能也靠轮询/下单响应，缺 user data stream 的实时成交回报**。
- 每 60s 醒一次的轮询模型，本质上**天然带了一层「定期对账」的骨架**——这是优势，应该把它显式强化成对账循环。

### 值得做成实验的（📋待评估，等用户拍板）
- **实验 E1｜下单幂等化**：每次下单前生成并本地持久化 `clientOrderId`；下单超时/异常时改为「先查询该 ID 状态，再决定重试」，绝不盲目重试。**直接消灭「双倍仓位」这个 15U 保证金下的爆仓级 bug。** 🧪 优先级最高。
- **实验 E2｜启动对账（startup reconciliation）**：bot 每次启动/重启时，先 REST 拉 `openOrders` + `position` + 最近 `myTrades`，与本地状态三方核对：发现裸仓（有仓无 SL/TP）→ 立即补挂止损或告警；发现幽灵单 → 清本地。**根治「重启后孤儿仓位裸奔」。**
- **实验 E3｜60s 轮询升级为周期对账**：把现有轮询循环里显式加一步「用交易所真实仓位覆盖本地仓位状态」，让「交易所是唯一真相源」制度化，而非只在启动时。
- **实验 E4｜可观测性**：把上述漂移事件（孤儿/幽灵/裸仓/重复成交）打成结构化日志 + 告警，喂给每日复盘——这正好接上「bot+复盘+研究+知识库」的自主迭代闭环，让系统能「发现自己出过什么错」。

### 迁移价值（用户主线：学技术）
「幂等键 + 唯一真相源 + 重连三层防御 + 对账循环」是**分布式系统可靠性的通用范式**：
- 换到美股代币（7 月底扩展）：现货同样有下单超时/重启对账问题，这套直接复用。
- 换到 AI agent / Claude-like 自主循环：agent 调用外部工具（下单、发消息、写文件）同样是「带副作用的重试」，`clientOrderId` 就是 agent 的 idempotency key，「对账循环」就是 agent 每轮开始前 re-observe 真实环境状态。**这不是交易专用技巧，是自主系统的通用内核。**

---

## 参考来源
- Binance Dev Community《How is exclude duplicate order execution by API?》— newClientOrderId 的坑与「靠 user data stream 确认」共识：https://dev.binance.vision/t/how-is-exclude-duplicate-order-execution-by-api/1585
- Binance Academy《Binance API: Understanding Order Status》— 唯一真相源、幂等更新、迟到成交/乱序边界、定期对账：https://academy.binance.com/en/articles/binance-api-understanding-order-status
- Binance 官方文档《USDⓈ-M Futures User Data Streams Connect》— listenKey 60min、连接 24h、消息排序：https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams
- Binance 官方文档《New Order (TRADE)》— newClientOrderId 规则、订单类型、REJECTED：https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api
- MatrixTrak《WebSocket Reconnection That Actually Works》— 三层防御、指数退避+jitter、心跳、序列号缺口检测：https://matrixtrak.com/blog/websocket-disconnects-trading-bots-reconnection
- MatrixTrak《Bot restart causes duplicate orders or orphan state》+ Reconciliation Kit — 孤儿/幽灵单、补录成交、仓位核对：https://matrixtrak.com/errors/crash-recovery-double-orders-on-startup
