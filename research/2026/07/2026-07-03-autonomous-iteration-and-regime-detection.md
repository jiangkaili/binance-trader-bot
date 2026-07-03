# 自主迭代系统 + 市场状态识别 / Autonomous Iteration & Market Regime Detection

> 日期：2026-07-03（周五）
> 类别：工程(自主迭代/Maker-Checker/A/B 灰度，约 60%) + 风控(市场状态识别/vol targeting，约 40%)
> 关联沉淀：`engineering/autonomous-iteration.md`（新建）/ `risk-management/market-regime.md`（新建）

---

## 0. 为什么选这两个主题

用户主线是「学技术」，且明确对「bot+复盘+研究+知识库 = 小型自主迭代系统 / 睡后代码自主循环」感兴趣。前三天已覆盖回测验证(robustness)、执行可靠性(system-architecture)、数据时间前沿(data-engineering/live-vs-backtest)。今天的工程主题直击「自主迭代」内核——**策略如何自动验证、防止自欺、A/B 与灰度、参数自动调优的安全边界**，这是把现有 bot 从「定时跑策略」升级为「自我进化的闭环系统」的关键一环。风控主题选「市场状态识别 + vol targeting」，扩展 bot 现有的 ADX/EMA 二值过滤，并为 7 月底扩美股代币（4-5 只高相关科技股）铺垫相关性风险。

---

# 主题一（工程，60%）：自主迭代系统 / Autonomous Strategy Iteration

## 1. 核心命题：把 bot 变成「Maker-Checker 闭环」

### 1.1 Maker-Checker（反思/评估器模式）是什么

在 Agentic AI 设计模式里，Maker-Checker 是双角色循环：一个 agent 产出（maker），另一个评估（checker），循环直到满足验收标准或达到最大迭代次数。它对应 Anthropic《Building Effective Agents》里的 **Evaluator-Optimizer** 模式，以及 Andrew Ng 的 **Reflection** 模式。

- **Maker**：产出代码/计划/信号（在我们的场景=「生成一个策略/参数变体」）
- **Checker**：对照验收标准评审（=「回测+样本外验证+实盘灰度」）
- **终止条件**：Checker 返回 `APPROVED` 或达到 `MAX_ITERATIONS` 安全阀
- 关键：两个角色**共享同一条上下文**（生成物 + 历次反馈），累积式精炼

来源：[Agentic AI Workshop — Maker-Checker (Reflection)](https://jeffrey-groneenberg.github.io/AI_Workshop_Agentic_Patterns/patterns/maker-checker/) · [Selectsys — Safe AI: Guardrails, QC, Maker-Checker](https://www.selectsys.com/blog/safe-ai-in-pc-ops-guardrails)（maker-checker 应用于「monetary or material updates」+ 不可变变更历史）

### 1.2 映射到交易系统：策略生命周期的 6 段闭环

把 Maker-Checker 套到我们的 bot，就是一条从研究到退役的流水线。每一阶段都是「上一阶段的 checker、下一阶段的 maker」：

```
 研究 → 回测(WFO/MC验证) → 影子(shadow) → 灰度(canary) → 全量(live) → 监控/退役
 maker   checker            checker        checker        maker(运行)   checker
```

关键洞察：**当前 bot 缺的是中间三个 checker**——影子、灰度、自动监控。现在的研究→回测→直接上实盘，跳过了「先不让新参数碰真钱、但用真行情验证」和「小仓位试运行」两道闸。这正是「自欺」（过拟合参数直接进实盘）的入口。

---

## 2. 影子交易 vs 灰度（Canary）：交易版的 A/B

### 2.1 Shadow Deployment（影子）

新模型/新参数对 **100% 真实行情**打分，但**输出只记录不下单**。老策略继续服务。

- **统计学超能力**：新旧看到完全相同的输入 → **配对观测(paired observations)**，共同噪声抵消，差异方差大幅下降。这比朴素 A/B 更容易检出微小改进。
- 零资金风险：新策略有 bug 也只是污染日志，伤不到本金。
- **致命局限**：影子只能验证「**模型输出是否不同**」（信号层面：新参数会不会在该进场时进场、滑点假设对不对），**无法验证下游行为指标**（真实 P&L、真实滑点、部分成交）。因为它的单从没真打出去。

来源：[MetricGate — Shadow Deployment vs Canary Rollout](https://metricgate.com/blogs/shadow-deployment-vs-canary/)（「Shadow measures model output differences, never user response」）

### 2.2 Canary（灰度）

把新策略以**小比例**（典型 1-5%）真实服务，其余留在 champion。这是**真正的 A/B**——输出触达市场，下游指标（P&L、滑点、延迟敏感行为）变得可观测。

- 样本与控制组**独立不配对**，方差不像影子那样抵消；比例越小，统计收敛越慢，所需样本越大。
- 真实但小的资金风险：bug 会真的亏小钱。
- **ML/策略必经 canary 的原因**：离线测试能过、上线却崩，根因是 **training-serving skew**（训练-服务偏差）。只有真实流量能暴露。

来源：[System Overflow — Canary vs Blue-Green vs Shadow](https://www.systemoverflow.com/learn/ml-ab-testing/ramp-up-strategies/trade-offs-canary-vs-blue-green-vs-shadow-deployment)（「models can pass offline tests but fail online due to training-serving skew」；canary 24-48h 1%→100%）

### 2.3 交易领域的标准范式：Champion-Challenger

- **Champion**：当前生产稳定版，服务全部/大部分流量。是性能基线、版本化不可变产物、**失败时的回退锚点**。
- **Challenger**：候选（新算法/重训版/不同架构），经 A/B / shadow / canary 评估。达标即晋升为新 champion，启动下一轮。用于对抗 **concept drift（概念漂移）**。
- 必须有 **rollback 策略**：challenger 失败 → 流量瞬间切回 champion。

来源：[Inferensys — Champion-Challenger: Safe ML Deployment Framework](https://inferensys.com/glossary/continuous-model-learning-systems/safe-model-deployment/champion-challenger)

### 2.4 对本 bot 的可操作映射（重点）

我们 bot 的现实约束：单标的 BTCUSDT、15U 保证金、5x。无法像 Web 服务那样按用户分流量。但 champion-challenger 仍可落地为「**时间分片 + 仓位分片**」：

| 阶段 | 交易领域含义 | 本 bot 落地方式 | 风险 |
|------|------------|----------------|------|
| Shadow | 新参数生成信号但不打单，日志记录「would-have-traded」，与 champion 信号配对比对 | 跑一个旁路进程读同样的 5m K线/funding，用候选参数算信号，只写日志不下单 | 0（不碰钱）|
| Canary | 新参数真实下单但极小仓位 | 候选参数仅用保证金的一小部分（如 3U）实盘跑 N 周，champion 仍正常跑 | 小真钱 |
| Promote | challenger 统计显著优于 champion | 用 WFO+MC 验证后切换 trader.yaml | — |
| Rollback | challenger 不达标 | champion 参数版本化保存，一键回退 | — |

**关键工程点**：trader.yaml 必须版本化（每次改参数 = 一个 champion 版本号），且保留上一版可回退。现在 v8 是「唯一三窗口全正」——这本身是**孤立尖峰警报**（见 6/30 robustness 笔记实验 A），更需要 champion-challenger 来防止「换参数=赌一把」。

---

## 3. 自主/无人值守交易的安全边界（Guardrails）

用户想要「睡后代码自主循环」——这恰恰是高风险场景。业界共识：**自主交易 agent 必须有分层治理，不能让 AI 直接拍板执行**。

### 3.1 Jump Trading 的 4 层控制栈（2024 案例）

报道：Jump 早期**无治理的 agentic AI 研究工具**触发了 3 次未预期的回测过拟合事件，若上线预计造成 **780 万美元实盘亏损**，随后为所有 agentic AI 工作流强制分层治理。其 4 层：

1. **Pre-trade**：下单前订单量/价格限价检查
2. **In-trade**：实时波动率熔断——1 分钟价格波动超 2% 暂停执行
3. **Post-trade**：成交后做市操纵检测 + 通讯 NLP 交叉分析
4. **EOD**：每日终模型性能对照 12+ 基线指标验证

据报道该栈使 2024 年非计划交易亏损下降 68%。

来源：[Fintech Frontier — Framework-Based AI Algo Trading Risk Management 2024-2026](https://cyjjf.com/framework-based-ai-algorithmic-trading-risk-management-2024-2026-high-frequency-trading-hft-risk-mitigation-market-manipulation-detection-trade-execution-error-prediction-guide)（FSB 2024 报告：2023 年 HFT 风险事件 72% 可用正式风控框架预防，平均可预防损失 210 万美元/起）

### 3.2 通用治理清单（适用于我们的 bot）

- ✅ **所有执行决策必经硬性门**：参数自动调优的产出**不得直接改 trader.yaml 进实盘**，必须先过 shadow→canary。AI/cron 只能「提议」，落地需灰度验证。
- ✅ **样本外验证 30 天+，覆盖高/低波动两种状态**
- ✅ **交易监控与 AI 逻辑同步**：实时标记异常自主行为（如短时密集下单）
- ✅ **可审计**：每个决策留痕（信号→funding→参数版本→成交），保留足够长时间

### 3.3 这正是我们 bot+复盘+研究+KB 系统的工程内核

用户的「bot 跑 / cron 复盘 / 研究建知识库 / 实验排队」本质就是一个自主迭代系统。对照 Maker-Checker：
- **Maker** = 研究产出新策略/参数候选（如本日报、实验 A-E）
- **Checker（当前已有）** = cron 复盘、WFO/MC 回测
- **Checker（当前缺失）** = shadow 影子验证、canary 灰度、自动晋升/回退门

**结论**：把 shadow + canary + champion 版本化补上，这个系统就从「定时跑+人工复盘」升级为「自我验证的闭环」——这正是用户想要的工程内核，且可迁移到任何 agent 方向。

---

## 4. 参数自动调优的安全边界

用户对「参数自动调优」感兴趣。自动调优最大的坑=过拟合+数据窥探。安全边界清单：

1. **用 WFO 而非单次 OOS**：单次样本外切分仍是静态的，参数只在一个历史语境最优；WFO 滚动重优化+拼接所有 OOS 结果，才是实盘真实估计。一致性分数(consistency score) >0.70 才算可用。
   - 来源：[BacktestMe — Walk-Forward Analysis](https://backtestme.com/guides/walk-forward-analysis) · [Algovantis — WFO vs Overfitting](https://algovantis.com/walk-forward-optimization-versus-overfitting-in-backtesting-research)
2. **找参数高原(plateau)而非尖峰**：选「邻域都稳定」的参数区，不选「单点最优」。v8 的「唯一三窗口全正」是尖峰警报。可用 plateau score 算法替代「取最优 in-sample」。
   - 来源：[Harbourfronts — Overfitting and Parameter Selection](https://blog.harbourfronts.com/2026/05/04/overfitting-and-parameter-selection-in-trading-strategies/)（引 arXiv:2501.03938）
3. **随机化检验(shuffle test)**：把策略跑在打乱顺序的价格上，若表现相当→边缘是噪声。
4. **OOS 交易数 ≥100** 才信任统计。
5. **每轮最大变更幅度(ratchet)**：自动调优单次只允许参数小幅移动（如 RSI ±2、SL/TP ±0.25%），禁止跨高原跳变。
6. **永远保留 champion 回退**。

---

# 主题二（风控，40%）：市场状态识别 + 波动率调仓

## 5. 为什么需要超越 ADX/EMA 的二值过滤

bot 现有：`ADX>25` 才允许新开仓 + `price>EMA200` 才做多。这是**二值、硬阈值**的 regime 门。问题：
- 阈值附近频繁切换 → 来回打脸
- 只区分「趋势/震荡」，不区分「平静/高波动」——而高波动正是均值回归被打穿的主因
- 不调仓位大小：15U 固定，高波动时风险暴露翻倍却不知

## 6. HMM 市场状态识别

### 6.1 核心思想

状态是**隐**的（bull/bear/sideways/high-vol），只能通过可观测数据（收益率、波动率、VIX）推断。HMM 用 EM 算法从数据学状态结构，无需人工阈值。

### 6.2 关键性质：状态持续性（Transition Persistence）

HMM 转移矩阵对角线高 → **每个状态高度自持续**，不会每天翻转。这是 HMM 对交易有用的根本原因：状态有惯性，可据此提前调整。

### 6.3 典型 4 状态（SPY 实测）

| 状态 | 均值日收益 | 日波动 | 含义 |
|------|-----------|--------|------|
| Bull | +0.078% | 0.63% | 上升修复，低波 |
| Bear | +0.009% | 1.25% | 回撤/风险偏好 off |
| Sideways | +0.067% | 0.72% | 区间震荡，最频繁 |
| High-Vol | +0.025% | 2.23% | 冲击事件（COVID/闪崩）|

- Bull/Sideways 的 Sharpe 强；High-Vol 是均值回归的死亡区。
- **必须 walk-forward 重训**（典型 5 年训练窗、每 3 个月重训），否则状态结构漂移。

来源：[isaacnicas — HMM Market Regime Classifier (SPY)](https://isaacnicas.github.io/market-regime-detection-hmm) · [QuantInsti — Regime-Adaptive Trading in Python](https://blog.quantinsti.com/regime-adaptive-trading-python) · [IEEE — HMM-Based Regime Detection with RL](https://ieeexplore.ieee.org/abstract/document/11038776)（HMM+RL 在 30 只道指股上 Sharpe/收益超基线）

## 7. 波动率调仓（Volatility Targeting）

### 7.1 公式

```
仓位规模 = 目标波动率 / 预测波动率
```

- 预测波动率低 → scale>1 加杠杆加暴露
- 预测波动率高 → scale<1 减暴露
- 结果：组合**实际波动**贴近目标，时间上更平滑可预测

### 7.2 为什么有效：波动率聚集(Volatility Clustering)

「平静孕育平静，动荡孕育动荡」——高波动日聚集、低波动日也聚集。这使得**近期波动率远比收益率可预测**。vol targeting 据此在波动率（往往也是回撤）刚抬头时减仓，**主要收益是压低回撤深度、改善风险调整收益**，对负波动率-收益关系的资产（如股票）最明显。

来源：[EC Assets — Volatility Targeting](https://www.ecassets.com/learn/volatility-targeting)（引 Harvey et al. 2018, JPM; Moreira & Muir 2017, JF）· [Tradewink — Advanced Position Sizing](https://www.tradewink.com/learn/advanced-position-sizing-strategies-guide)

### 7.3 ⚠️ 致命陷阱：顺周期性（Procyclicality）

这是必须记进风控手册的坑：

- **波动率低于目标时加杠杆** → 低波期积累大杠杆头寸
- **波动率+相关性同时飙升时被迫去杠杆** → 在最差时点集中抛售，**强化下跌**
- 2020 COVID 抛售：全球约 **2 万亿美元** AUM 的波动率策略、约 100 只 risk parity 基金（3000 亿美元）被迫机械减仓，放大市场卖压
- 2022：Bridgewater All Weather（risk parity）创纪录 **-22%**——股债相关性从长期 -0.20 飙到 +0.65，分散化在最需要时崩溃，2x 债券杠杆放大亏损

来源：[ECB Financial Stability Review 2020 — Volatility-targeting strategies and the market sell-off](https://www.ecb.europa.eu/press/financial-stability-publications/fsr/focus/2020/html/ecb.fsrbox202005_02~f6616db9be.en.html) · [Bridgewater Alpha-Beta Framework](https://navnoorbawa.substack.com/p/bridgewaters-alpha-beta-framework)

**对本 bot 的教训**：机械 vol targeting 会变成「跌完才减仓=卖低」。正确姿势是**预防性**——波动率刚抬头（还没大跌）就缩仓，而非等崩了再追减。对小资金 15U 更要小心：vol-targeting 触发频繁调仓，手续费/滑点可能吃掉全部收益。

## 8. 对本 bot 的可操作启发（综合）

### 8.1 短期可评估（不改代码，先研究）
- **用 ATR 替代/补充 ADX**：ATR 既能量波动又能间接判状态。5m ATR 处于近 N 根高位时 → 判为 high-vol regime → 跳过新开仓（均值回归在高波动易被打穿）。这是对现有 ADX>25 的低成本增强。
- **vol-targeted 仓位微调**：在 15U 基础上按 `scale = target_ATR / current_ATR` 做软调整（如 0.5x-1.5x），高波动缩仓、低波动略加。⚠️ 必须回测手续费影响（5m 频繁调仓成本高）。

### 8.2 中期（扩美股前必须做）
- **逐标的 regime 检测**：扩到 4-5 只美股代币时，每只独立跑 regime（不能假设同步）。
- **相关性风险**：4-5 只科技股高相关 ≠ 5 个独立机会。仓位不能简单相加，要用相关性调整总暴露（risk parity 思路：按风险贡献而非金额分配）。2022 All Water -22% 就是相关性断裂的反面教材——「分散」在相关性飙升时失效。
- 现货无 funding rate：funding z-score 过滤对美股代币**不适用**，需逐标的回测替代过滤。

### 8.3 长期（工程内核）
- HMM regime + vol target 是 ML 信号，引入后**必须**走主题一的 shadow→canary 流程，不能直接进实盘。这也顺带验证了主题一的工程价值：风控增强本身就是 challenger，需要 champion-challenger 框架来安全引入。

---

## 9. 今日核心结论

1. **自主迭代 = Maker-Checker 闭环**：当前 bot+复盘+研究+KB 已是半套自主系统，缺的是 shadow/canary/champion 版本化三道闸。补上即升级为「自我验证闭环」，且是可迁移到任何 agent 方向的硬工程。
2. **shadow vs canary 不可混用**：shadow 验证信号(零风险、配对方差小)、canary 验证真实 P&L(小真钱、独立样本)。交易里两者都要，先 shadow 后 canary。
3. **自主交易 agent 必须分层治理**：AI/cron 只能提议不能直接改实盘参数，所有变更经 WFO+灰度门，保留 champion 回退。Jump Trading 4 层栈是参考蓝本。
4. **vol targeting 是双刃剑**：能压回撤，但顺周期性会在崩盘时被迫卖低。小资金+高频调仓要先回测手续费。
5. **扩美股前先解决相关性**：4-5 只科技股高相关，简单加仓=集中风险；2022 risk parity -22% 是警示。

## 10. 待落地实验（新增，登记进手册）
- **实验 F1：Shadow 信号旁路** 📋待评估 — 旁路进程读同样行情，用候选参数算信号只写日志不下单，与 champion 信号配对统计。零成本验证参数变体，是 champion-challenger 的第一步。详见 `engineering/autonomous-iteration.md`。
- **实验 F2：trader.yaml 版本化 + 一键回退** 📋待评估 — 每次参数变更存为一个 champion 版本（含回测摘要），失败可秒回退。是 canary 的安全前提。详见 `engineering/autonomous-iteration.md`。
- **实验 G1：ATR high-vol 过滤** 📋待评估 — 5m ATR 处于近 N 根高位时跳过新开仓，低成本增强现有 ADX/EMA 过滤。详见 `risk-management/market-regime.md`。
- **实验 G2：vol-targeted 软调仓** 📋待评估 — 15U 基础按 ATR 比例 0.5x-1.5x 软调，⚠️ 必须先回测 5m 调仓手续费。详见 `risk-management/market-regime.md`。

## 11. 参考来源汇总
- [Agentic AI Workshop — Maker-Checker (Reflection) Pattern](https://jeffrey-groneenberg.github.io/AI_Workshop_Agentic_Patterns/patterns/maker-checker/)
- [Selectsys — Safe AI in P&C Ops: Guardrails, QC and Maker-Checker](https://www.selectsys.com/blog/safe-ai-in-pc-ops-guardrails)
- [Inferensys — Champion-Challenger: Safe ML Model Deployment Framework](https://inferensys.com/glossary/continuous-model-learning-systems/safe-model-deployment/champion-challenger)
- [MetricGate — Shadow Deployment vs Canary Rollout](https://metricgate.com/blogs/shadow-deployment-vs-canary/)
- [System Overflow — Canary vs Blue-Green vs Shadow Trade-offs](https://www.systemoverflow.com/learn/ml-ab-testing/ramp-up-strategies/trade-offs-canary-vs-blue-green-vs-shadow-deployment)
- [Fintech Frontier — Framework-Based AI Algo Trading Risk Management 2024-2026](https://cyjjf.com/framework-based-ai-algorithmic-trading-risk-management-2024-2026-high-frequency-trading-hft-risk-mitigation-market-manipulation-detection-trade-execution-error-prediction-guide)
- [BacktestMe — Walk-Forward Analysis: Gold Standard](https://backtestme.com/guides/walk-forward-analysis)
- [Algovantis — WFO vs Overfitting in Backtesting](https://algovantis.com/walk-forward-optimization-versus-overfitting-in-backtesting-research)
- [Harbourfronts — Overfitting and Parameter Selection (arXiv:2501.03938)](https://blog.harbourfronts.com/2026/05/04/overfitting-and-parameter-selection-in-trading-strategies/)
- [isaacnicas — HMM Market Regime Classifier (SPY)](https://isaacnicas.github.io/market-regime-detection-hmm)
- [QuantInsti — Market Regime using HMM (Python)](https://blog.quantinsti.com/regime-adaptive-trading-python)
- [IEEE — HMM-Based Regime Detection with RL (2025)](https://ieeexplore.ieee.org/abstract/document/11038776)
- [EC Assets — Volatility Targeting](https://www.ecassets.com/learn/volatility-targeting)
- [Tradewink — Advanced Position Sizing: Vol Targeting](https://www.tradewink.com/learn/advanced-position-sizing-strategies-guide)
- [ECB Financial Stability Review 2020 — Vol-targeting & market sell-off](https://www.ecb.europa.eu/press/financial-stability-publications/fsr/focus/2020/html/ecb.fsrbox202005_02~f6616db9be.en.html)
- [Bridgewater Alpha-Beta / Risk Parity Framework](https://navnoorbawa.substack.com/p/bridgewaters-alpha-beta-framework)
