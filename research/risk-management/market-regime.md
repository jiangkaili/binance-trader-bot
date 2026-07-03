# 风控：市场状态识别 + 波动率调仓 / Market Regime & Volatility Targeting

> 风控类知识沉淀。
> 主题：超越 bot 现有的 ADX/EMA 二值过滤，用市场状态(regime)识别 + 波动率调仓(vol targeting)做更精细的风控；并为 7 月底扩美股代币铺垫相关性风险。
> 服务对象：binance-trader-bot（BTCUSDT 永续，RSI 均值回归 + funding z-score，5x/15U）。

---

## 1. 当前 bot 的 regime 处理与不足

bot 现有：`ADX>25` 才允许新开仓 + `price>EMA200` 才做多（`trend_filter_adx_threshold: 25` / `trend_ema_filter_enabled: true`）。这是**二值硬阈值** regime 门。不足：
- 阈值附近频繁切换 → 来回打脸
- 只区分「趋势/震荡」，不区分「平静/高波动」——而高波动正是均值回归被打穿的主因
- 不调仓位：15U 固定，高波动时风险暴露翻倍却不知

---

## 2. HMM 市场状态识别

### 2.1 核心思想
状态是**隐**的(bull/bear/sideways/high-vol)，只通过可观测数据(收益率/波动率/VIX)推断。HMM 用 EM 从数据学状态结构，无需人工阈值。

### 2.2 关键性质：状态持续性(Transition Persistence)
转移矩阵对角线高 → **状态高度自持续**，不会每天翻转。这是 HMM 对交易有用的根本：状态有惯性，可提前调整。

### 2.3 典型 4 状态（SPY 实测，可类比 BTC）
| 状态 | 均值日收益 | 日波动 | 含义 | 均值回归表现 |
|---|---|---|---|---|
| Bull | +0.078% | 0.63% | 上升修复低波 | 一般(易追高) |
| Bear | +0.009% | 1.25% | 回撤/风险偏好off | 差(逆势) |
| Sideways | +0.067% | 0.72% | 区间震荡,最频繁 | ✅ 最佳环境 |
| High-Vol | +0.025% | 2.23% | 冲击事件(COVID/闪崩) | ❌ 死亡区 |

- Bull/Sideways Sharpe 强；High-Vol 是均值回归死亡区——这正是 bot 应跳过开仓的状态。
- **必须 walk-forward 重训**（典型 5 年训练窗、每 3 个月重训），否则状态结构漂移。
- HMM+RL 组合在 30 只道指股上 Sharpe/收益超基线（IEEE 2025）。

来源：[isaacnicas — HMM Regime Classifier (SPY)](https://isaacnicas.github.io/market-regime-detection-hmm) · [QuantInsti — Regime-Adaptive Python](https://blog.quantinsti.com/regime-adaptive-trading-python) · [IEEE — HMM+RL 2025](https://ieeexplore.ieee.org/abstract/document/11038776)

---

## 3. 波动率调仓（Volatility Targeting）

### 3.1 公式
```
仓位规模 = 目标波动率 / 预测波动率
```
- 预测波动率低 → scale>1 加暴露
- 预测波动率高 → scale<1 减暴露
- 组合**实际波动**贴近目标，时间上更平滑可预测

### 3.2 为什么有效：波动率聚集(Volatility Clustering)
「平静孕育平静，动荡孕育动荡」——高波动日聚集、低波动日也聚集。**近期波动率远比收益率可预测**。vol targeting 在波动率(往往也是回撤)刚抬头时减仓，**主要收益=压低回撤深度、改善风险调整收益**，对负波动率-收益关系资产(如股票)最明显。

来源：[EC Assets — Volatility Targeting](https://www.ecassets.com/learn/volatility-targeting)（引 Harvey et al. 2018 JPM; Moreira & Muir 2017 JF）· [Tradewink — Advanced Position Sizing](https://www.tradewink.com/learn/advanced-position-sizing-strategies-guide)

### 3.3 ⚠️ 致命陷阱：顺周期性(Procyclicality)——必须记进风控
- **波动率低于目标时加杠杆** → 低波期积累大杠杆头寸
- **波动率+相关性同时飙升时被迫去杠杆** → 最差时点集中抛售，**强化下跌**
- 2020 COVID：全球约 **2 万亿美元** AUM 波动率策略、约 100 只 risk parity 基金(3000 亿美元)被迫机械减仓，放大卖压
- 2022：Bridgewater All Weather 创纪录 **-22%**——股债相关性从长期 -0.20 飙到 +0.65，分散化在最需要时崩溃，2x 债券杠杆放大亏损

来源：[ECB FSR 2020 — Vol-targeting & sell-off](https://www.ecb.europa.eu/press/financial-stability-publications/fsr/focus/2020/html/ecb.fsrbox202005_02~f6616db9be.en.html) · [Bridgewater Alpha-Beta Framework](https://navnoorbawa.substack.com/p/bridgewaters-alpha-beta-framework)

**对本 bot 教训**：机械 vol targeting 会变「跌完才减仓=卖低」。正确=**预防性**——波动率刚抬头(还没大跌)就缩仓，而非等崩了追减。小资金 15U 更要小心：频繁调仓手续费/滑点可能吃掉全部收益。❌ 不适用机械满版 vol targeting（15U+5m 频繁调仓成本过高）；🧪 仅评估软调仓(0.5x-1.5x)。

---

## 4. 扩美股代币的相关性风险（7 月底路线铺垫）

用户计划扩 4-5 只美股代币(tokenized stocks)。关键风险：
- **高相关 ≠ 独立机会**：4-5 只科技股高度相关，简单加仓=集中风险。
- **风险贡献分配**：应用 risk parity 思路——按风险贡献(非金额)分配，相关性高则总暴露要打折。
- **相关性会断裂**：2022 All Water -22% 即股债相关性飙升(常态 -0.20→+0.65)击穿分散假设。科技股在抛售中相关性会趋向 1，分散在最需要时失效。
- **现货无 funding rate**：funding z-score 过滤对美股代币**不适用**，需逐标的回测替代过滤。

来源：[Bridgewater Alpha-Beta / Risk Parity](https://navnoorbawa.substack.com/p/bridgewaters-alpha-beta-framework) · [Grokipedia — Risk Parity](https://grokipedia.com/page/Risk_parity)

📋 待评估：扩美股前必须逐标的 regime 检测 + 相关性矩阵 + 相关性调整的总暴露上限。

---

## 5. 对本 bot 的可操作启发
- **实验 G1：ATR high-vol 过滤** 📋待评估 — 5m ATR 处于近 N 根高位时跳过新开仓(均值回归在高波动易被打穿)。低成本增强现有 ADX/EMA 过滤，无需 ML。
- **实验 G2：vol-targeted 软调仓** 📋待评估 — 15U 基础按 `scale=target_ATR/current_ATR` 做 0.5x-1.5x 软调，高波动缩仓低波动略加。⚠️ 必须先回测 5m 调仓手续费影响。
- 现有 ADX/EMA 二值过滤：✅已实现（`trend_filter_adx_threshold:25` / `trend_ema_filter_enabled:true`），是 regime 门的最简版。
- HMM regime + vol target 是 ML 信号：❌ 不直接进实盘，引入后必走 `engineering/autonomous-iteration.md` 的 shadow→canary 流程（风控增强本身也是 challenger）。

---

## 6. 与其他风控文件的关系
- `stop-loss.md`：止损技术——vol regime 高时止损应更宽或停手
- `position-sizing.md`：仓位管理——vol targeting 是动态仓位的一种，本文是其 regime 版
- `drawdown-control.md`：回撤控制——vol targeting 主要收益即压回撤，但顺周期是反例
- `tail-risk.md`：极端风险——相关性断裂、vol-targeting 强化抛售属尾部风险
