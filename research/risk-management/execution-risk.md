# 执行风险 / Execution Risk

> 风控知识库章节。主题：滑点、手续费、资金费率成本、订单类型、延迟，以及**执行层故障导致的风险敞口**（本篇重点）。
> 服务对象：binance-trader-bot（BTCUSDT 永续，低杠杆小资金 15U）。
> 工程实现细节见 `../engineering/system-architecture.md`；本篇只记录「风险敞口」视角。

---

## 执行层故障 = 隐性风险敞口（2026-07-01）

真实运行的 bot，除了策略/市场风险，还有一类「本不该发生」的执行风险。对 15U 小保证金尤其致命：

### 1. 重复下单风险 → 双倍仓位 → 爆保证金 🧪高优先
- 成因：下单请求超时/响应丢失，bot 以为失败去重试，实际两单都成交。
- 后果：仓位翻倍，名义价值从 75U 冲到 150U，**直接超出 15U 保证金能扛的范围**，等于被迫高杠杆裸奔。
- ⚠️ `newClientOrderId` **不能完全防**：上一单已成交时，同 ID 新单会被接受。
- 缓解：下单前持久化 clientOrderId；超时改「先查询该单状态再决定重试」，绝不盲目重试。详见工程文件实验 E1。
- 状态：📋待评估（强烈建议做，属爆仓级 bug）。

### 2. 孤儿仓位风险 → 无 SL/TP 保护的裸仓 📋
- 成因：进程崩溃 / WSL 重启 / 网络断，重启后 bot 内存里「没仓位」，但交易所里真有一个开着的仓位。
- 后果：这个仓位**没有 SL/TP 挂单保护**，遇到不利波动会无限亏损，突破用户要求的「硬止损」底线。
- 缓解：启动对账——每次启动先 REST 拉真实 position + openOrders，发现裸仓立即补挂 SL/TP 或告警强平。详见工程文件实验 E2。
- 状态：📋待评估。**与用户「硬止损必须落地」的要求直接相关，风控优先级高。**

### 3. 重复计数风险 → 日/周亏损熔断误触发 📋
- 成因：断线重连后消息乱序/重放，同一笔成交被算两次。
- 后果：本地 PnL 记错。可能把一笔亏损算成两笔 → **误触发 `daily_loss_pct: 0.25` / `weekly_loss_pct: 0.40` 熔断**，白白停机错过行情；或反向少算导致熔断该触发没触发。
- 缓解：每个成交事件按唯一 ID 幂等处理，只计一次。
- 状态：📋待评估。

### 4. REJECTED 误判风险 📋
- 成因：保证金不足等原因订单被交易所 `REJECTED`，永不进 open 状态。
- 后果：若 bot 误当「已挂单」，会以为有仓/有保护，实际什么都没有。
- 缓解：明确捕获 REJECTED 状态并告警，不计入持仓。
- 状态：📋待评估。

---

## 核心原则
- **交易所是唯一真相源**：本地状态与交易所不一致，无条件信交易所。
- **对账不只在启动**：启动 + 定期(可搭 60s 轮询) + 每次重连后都核对，把「裸仓/双仓/重复计数」在造成损失前就修掉。

## 来源
- Binance Academy《Understanding Order Status》：https://academy.binance.com/en/articles/binance-api-understanding-order-status
- Binance Dev Community 重复下单讨论：https://dev.binance.vision/t/how-is-exclude-duplicate-order-execution-by-api/1585
- MatrixTrak Crash Recovery：https://matrixtrak.com/errors/crash-recovery-double-orders-on-startup
- 完整工程实现：`../engineering/system-architecture.md`
