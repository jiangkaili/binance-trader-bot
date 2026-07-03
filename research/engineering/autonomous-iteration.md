# 工程能力：自主迭代系统 / Autonomous Strategy Iteration

> 工程类知识沉淀（量化工程能力 60% 重心）。
> 主题：把交易 bot 从「定时跑策略」升级为「自我验证的闭环系统」——Maker-Checker、champion-challenger、shadow/canary 灰度、参数自动调优的安全边界。
> 这是任何「自主 agent / 睡后代码自主循环」的通用工程内核，可迁移到交易以外的 AI agent 方向。
> 服务对象：binance-trader-bot（BTCUSDT 永续，RSI 均值回归 + funding z-score）。

---

## 1. 一句话记忆

**自主迭代 = Maker(产出候选) → Checker(验证) 循环，验证不通过绝不进实盘。当前 bot 缺的是中间三道闸：影子、灰度、自动晋升/回退。**

---

## 2. Maker-Checker（反思/评估器模式）

双角色循环：maker 产出，checker 评审，循环到满足验收标准或达 `MAX_ITERATIONS`。对应 Anthropic《Building Effective Agents》的 **Evaluator-Optimizer**、Andrew Ng 的 **Reflection**。两角色共享同一条上下文（生成物+历次反馈），累积式精炼。

- 交易映射：maker = 研究/调参产出新策略变体；checker = 回测(WFO/MC) → 影子 → 灰度 → 监控。每一阶段是上一阶段的 checker、下一阶段的 maker。
- 适用：质量需在产出前验证、有明确验收标准（策略通过样本外+灰度）。不适用：单 pass 即够、无迭代价值。

来源：[Agentic AI Workshop — Maker-Checker Pattern](https://jeffrey-groneenberg.github.io/AI_Workshop_Agentic_Patterns/patterns/maker-checker/) · [Selectsys — Guardrails/QC/Maker-Checker](https://www.selectsys.com/blog/safe-ai-in-pc-ops-guardrails)（maker-checker 用于「monetary or material updates」+ 不可变变更历史 + 活动日志）

**对本 bot**：用户「bot 跑 / cron 复盘 / 研究建 KB / 实验排队」已是半套自主系统。maker=研究产候选，checker(已有)=cron 复盘+WFO/MC，checker(缺失)=shadow/canary/自动晋升门。📋 待评估。

---

## 3. Shadow vs Canary：交易版 A/B

### 3.1 Shadow（影子）
新参数对 100% 真实行情打分，**输出只记录不下单**，老策略继续服务。
- **配对观测**：新旧看到相同输入 → 共同噪声抵消，差异方差大幅下降 → 比朴素 A/B 更易检出微小改进。
- 零资金风险：bug 只污染日志。
- **致命局限**：只能验证「模型输出是否不同」（信号层：该不该进场、滑点假设对不对），**无法验证下游行为**（真实 P&L/滑点/部分成交），因为单没真打。

### 3.2 Canary（灰度）
新策略以小比例（1-5%）真实服务，其余留 champion。**真 A/B**——输出触达市场，下游指标可观测。
- 样本与控制组**独立不配对**，方差不抵消；比例越小收敛越慢、所需样本越大。
- 小真钱风险：bug 真亏小钱。
- **必经 canary 的根因**：training-serving skew——离线测试能过、上线却崩，只有真流量能暴露。

### 3.3 关键区分（别混用）
| | Shadow | Canary |
|---|---|---|
| 验证对象 | 模型输出(信号) | 下游行为(真实P&L/滑点) |
| 样本 | 配对(同输入) | 独立(分流量) |
| 资金风险 | 0 | 小 |
| 能否测 P&L | ❌ 不能 | ✅ 能 |
| 顺序 | 先 | 后 |

来源：[MetricGate — Shadow vs Canary](https://metricgate.com/blogs/shadow-deployment-vs-canary/) · [System Overflow — Canary/Blue-Green/Shadow Trade-offs](https://www.systemoverflow.com/learn/ml-ab-testing/ramp-up-strategies/trade-offs-canary-vs-blue-green-vs-shadow-deployment)

### 3.4 交易领域标准范式：Champion-Challenger
- **Champion**：当前生产稳定版，服务全部/大部分流量；性能基线；版本化不可变产物；**失败回退锚点**。
- **Challenger**：候选(新算法/重训/不同架构)，经 A/B/shadow/canary 评估，达标晋升为新 champion，启动下一轮。用于对抗 concept drift。
- **必须有 rollback**：challenger 失败 → 流量瞬间切回 champion。

来源：[Inferensys — Champion-Challenger Framework](https://inferensys.com/glossary/continuous-model-learning-systems/safe-model-deployment/champion-challenger)

### 3.5 本 bot 落地（时间分片 + 仓位分片，因单标的无法按用户分流量）
| 阶段 | 含义 | 本 bot 方式 | 风险 |
|---|---|---|---|
| Shadow | 新参数算信号不下单，日志配对比对 | 旁路进程读同 5m K线/funding，候选参数算信号只写日志 | 0 |
| Canary | 真实下单极小仓位 | 候选参数用保证金一小部分(如 3U)实盘跑 N 周 | 小真钱 |
| Promote | challenger 统计显著优于 champion | WFO+MC 验证后切 trader.yaml | — |
| Rollback | challenger 不达标 | champion 版本化保存，一键回退 | — |

**关键工程点**：trader.yaml 必须版本化（每次改=一个 champion 版本号），保留上一版可回退。v8「唯一三窗口全正」本身是**孤立尖峰警报**，更需 champion-challenger 防「换参数=赌一把」。📋 待评估（实验 F1/F2）。

---

## 4. 自主/无人值守交易的安全边界（Guardrails）

用户要「睡后代码自主循环」=高风险。业界共识：**自主交易 agent 必须分层治理，AI 不得直接拍板执行**。

### 4.1 Jump Trading 4 层控制栈（2024）
报道：Jump 早期**无治理 agentic AI 工具**触发 3 次回测过拟合，若上线预计 **780 万美元实盘亏损**，随后强制分层治理。4 层：
1. Pre-trade：下单前订单量/价格限价检查
2. In-trade：实时波动率熔断(1min 价格波动>2% 暂停)
3. Post-trade：成交后市场操纵检测 + 通讯 NLP 交叉
4. EOD：每日终模型性能对照 12+ 基线验证

该栈使 2024 非计划亏损下降 68%。FSB 2024 报告：2023 HFT 风险事件 **72% 可用正式框架预防**，平均可预防损失 210 万美元/起。

来源：[Fintech Frontier — AI Algo Trading Risk Management 2024-2026](https://cyjjf.com/framework-based-ai-algorithmic-trading-risk-management-2024-2026-high-frequency-trading-hft-risk-mitigation-market-manipulation-detection-trade-execution-error-prediction-guide)

### 4.2 通用治理清单（适用本 bot）
- ✅ **执行决策必经硬性门**：参数自动调优产出**不得直接改 trader.yaml 进实盘**，必先 shadow→canary。AI/cron 只能「提议」。
- ✅ 样本外验证 30 天+，覆盖高/低波动两状态
- ✅ 交易监控与 AI 逻辑同步：实时标异常自主行为(短时密集下单)
- ✅ 可审计：每决策留痕(信号→funding→参数版本→成交)，保留足够长

### 4.3 这正是用户系统的工程内核
bot+复盘+研究+KB = 自主迭代系统。补上 shadow+canary+champion 版本化，就从「定时跑+人工复盘」升级为「自我验证闭环」——可迁移到任何 agent 方向的硬工程。✅ 高度契合用户主线。

---

## 5. 参数自动调优的安全边界

自动调优最大坑=过拟合+数据窥探。安全清单：
1. **用 WFO 而非单次 OOS**：单次切分仍静态；WFO 滚动重优化+拼接所有 OOS=实盘真实估计。一致性分数>0.70 才可用。来源：[BacktestMe](https://backtestme.com/guides/walk-forward-analysis) · [Algovantis](https://algovantis.com/walk-forward-optimization-versus-overfitting-in-backtesting-research)
2. **找高原(plateau)非尖峰**：选「邻域稳定」参数区，不选「单点最优」。v8「唯一全正」是尖峰警报。plateau score 算法替代「取最优 in-sample」。来源：[Harbourfronts (arXiv:2501.03938)](https://blog.harbourfronts.com/2026/05/04/overfitting-and-parameter-selection-in-trading-strategies/)
3. **shuffle test**：跑在打乱顺序价格上，表现相当→边缘是噪声。
4. **OOS 交易数 ≥100** 才信任统计。
5. **每轮最大变更(ratchet)**：单次只允许参数小幅移动(RSI±2、SL/TP±0.25%)，禁跨高原跳变。
6. **永远保留 champion 回退**。

> 与 `robustness.md`（回测验证方法论）互补：robustness 讲「怎么验证一个参数好不好」，本文讲「自动产出好参数后怎么安全地让它进实盘」。

---

## 6. 待落地实验（本主题）
- **实验 F1：Shadow 信号旁路** 📋待评估 — 旁路进程读同行情，候选参数算信号只写日志不下单，与 champion 信号配对统计。零成本验证参数变体，champion-challenger 第一步。
- **实验 F2：trader.yaml 版本化 + 一键回退** 📋待评估 — 每次参数变更存为一个 champion 版本(含回测摘要)，失败秒回退。是 canary 的安全前提。
- **实验 F3：自动调优 ratchet 边界** 📋待评估 — 若做参数自动搜索，单轮变更幅度硬限制(RSI±2/SL·TP±0.25%)，跨高原跳变必须人工确认。

---

## 7. 与其他工程文件的关系
- `robustness.md`：参数验证方法论(WFO/MC/高原)——本文「调优产出后怎么验证」的上游
- `system-architecture.md`：执行层可靠性(幂等/重连/对账)——本文「灰度阶段」的执行底座
- `live-vs-backtest.md`：实盘-回测归因——本文 canary 阶段要对照的基准
- `data-engineering.md`：时间前沿/as-of 对齐——shadow/canary 都必须用闭合K线，否则伪信号
