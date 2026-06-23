# 代码审查与重构方案 — 2026-06-23

> 目标：让别人 clone 下来 5 分钟看懂、改两行就能用。
> 范围：核心交易循环 `scripts/live_trader.py` + `gridtrader/quant/*`。

## 一、当前最严重的 7 个问题

### 1. live_trader.py 是 836 行的"上帝类"
`LiveTrader` 一个类塞了 25 个方法，覆盖了：网络/认证、行情、下单、止损止盈、风控、状态持久化、信号循环、日志。一个类干七件事是教科书级别的反例。

### 2. 模块级全局常量 + YAML 加载混在脚本顶部
第 86-120 行 `_load_config()` 在导入时直接读 YAML 并把结果赋给 18 个 ALL_CAPS 全局变量（`SYMBOL`, `LEVERAGE`, `RSI_PERIOD` ...）。后果：
- 单元测试无法换配置（导入即固化）
- 类方法里同时用 `self.symbol` 和全局 `LEVERAGE`，风格不一致
- `gridtrader/quant/config.py` 里已经有 `@dataclass QuantSettings` 但**完全没人用**

### 3. 模块文档字符串重复了一整段
第 6-32 行 USAGE / OUTPUTS / EMERGENCY STOP 写了一次，第 35-61 行**一字不差地又写了一遍**。复制粘贴留下的 bug。

### 4. Binance API 调用散落在 LiveTrader 里
`call / fetch_account / set_leverage / get_klines / get_position / market_order / cancel_all_orders / place_exchange_stops` 8 个方法直接拼 URL 和 params。换交易所或加交易对要改这一坨。

### 5. 风控逻辑写得脏
`can_open_new()` 一个方法里硬编码读了 3 个文件 (`data/KILLSWITCH`, `data/COOLDOWN_UNTIL`, `data/trades.db`)，还直接 `import sqlite3` 绕过自己的 `Store` 抽象（line 400）。同一个文件路径硬编码 5 次。

### 6. 类型不安全
`Position` 用 `dict` 传 (`{"side": "LONG", "qty": ..., ...}`)，到处 `pos["side"] == "LONG"` 这种字符串比较。`gridtrader/quant/risk.py` 里已经有 `@dataclass Position` 但**也没人用**。

### 7. 测试覆盖空白
`tests/` 只覆盖了 strategies / indicators / backtest_risk 三个纯函数模块。**核心交易循环 live_trader 没有一行测试**。线上跑的真金白银代码 0 测试。

---

## 二、其他规范化问题

| 类别 | 问题 | 影响 |
|------|------|------|
| 日志 | `log()` 同时 print + 写文件 + 写 DB，无 level 过滤 | 调试关不掉 |
| 配置 | `.env.testnet` 和 `config/trader.yaml` 二选一逻辑混乱 | 启动语义不清 |
| 路径 | `data/trades.db`、`data/KILLSWITCH` 硬编码 | 跨机部署难 |
| 错误处理 | 多处 `except Exception: pass` | bug 静默消失 |
| 命名 | `_handle_external_close` / `_check_streak` 私有方法和公开方法混排 | 看不出边界 |
| import | `import yaml as _yaml`（line 84），其他地方又 `from xxx import yyy` | 风格不一致 |
| docstring | 大部分方法没 docstring，几个有的还过时 | 维护痛苦 |

---

## 三、重构后的目录结构

```
binance_grid_trader/
├── README.md                          ← 5 分钟上手
├── config/
│   └── trader.yaml                    ← 唯一配置入口
├── trader/                            ← 新包（替代 scripts/live_trader.py）
│   ├── __init__.py
│   ├── __main__.py                    ← python -m trader 即可运行
│   ├── config.py                      ← dataclass，校验配置
│   ├── exchange/
│   │   ├── __init__.py
│   │   ├── base.py                    ← Exchange 抽象基类
│   │   └── binance_futures.py         ← 所有 Binance HTTP 调用
│   ├── models.py                      ← Position/Signal/OrderResult dataclass
│   ├── risk.py                        ← RiskManager（killswitch、cooldown、daily/weekly cap）
│   ├── state.py                       ← StateStore（JSON + SQLite 持久化）
│   ├── strategy.py                    ← Strategy 接口 + RSI 实现（瘦的）
│   ├── trader.py                      ← Trader 主类（只剩 ~150 行）
│   └── logging_setup.py               ← logging.getLogger，分 level
├── gridtrader/                        ← 老代码保留（向后兼容）
│   └── quant/
│       └── ... (现有的策略/指标/回测保留供 backtest 用)
├── scripts/
│   ├── backtest.py
│   ├── check_position.py
│   ├── fetch_klines.py
│   └── (其他工具脚本，每个 < 100 行)
├── tests/
│   ├── test_risk.py                   ← 新增：killswitch、cooldown 单测
│   ├── test_exchange.py               ← 新增：用 responses 库 mock Binance
│   ├── test_state.py                  ← 新增：状态持久化往返
│   └── test_trader_loop.py            ← 新增：用 fake exchange 跑完整循环
├── docs/
│   ├── refactor/                      ← 本文件
│   ├── architecture.md                ← 给新人看的图
│   └── runbook.md                     ← 故障处理手册
└── reports/                           ← 每日分析（已存在）
```

---

## 四、重构示例：核心循环对比

### 现在（scripts/live_trader.py:650-747，98 行）
```python
def tick(self) -> None:
    self.tick_count += 1
    self._maybe_resync_time()
    if self._banned_until and time.time() < self._banned_until:
        ...
    if not self.dry_run:
        self.fetch_account()
    self.reset_daily()
    if not self.dry_run:
        prev_position = self.position
        self.position = self.get_position()
        if prev_position is not None and self.position is None:
            self._handle_external_close(prev_position)
    if self.check_position_stop_loss():
        self.close_position("stop_loss")
        return
    # ... 80 多行
```

### 重构后（trader/trader.py，~25 行）
```python
def tick(self) -> None:
    self.exchange.heartbeat()                          # 时钟、IP ban 自管
    self.account = self.exchange.fetch_account()
    self.risk.refresh(self.account)                    # 日界、周界
    pos = self.exchange.get_position(self.cfg.symbol)
    if self.state.position and pos is None:
        self._handle_external_close(self.state.position)
    self.state.position = pos
    bars = self.exchange.klines(self.cfg.symbol, self.cfg.interval)
    if len(bars) < self.cfg.warmup_bars:
        return
    signal = self.strategy.evaluate(bars)
    decision = self.risk.decide(signal, pos, self.account)
    self._execute(decision)
    self.state.checkpoint()
```

可读性差异：现在一个 `tick` 函数要看 100 行 8 处分支才知道在干什么。重构后从函数名就读出顺序。

---

## 五、分阶段实施计划（线上账户有钱跑着，不能一次推翻）

### Phase 1 — 安全清理（零行为变更，1-2 小时）
- [ ] 删除重复的 docstring（第 35-61 行）
- [ ] 把 `_load_config()` 改成函数，去掉模块级全局变量
- [ ] 提取硬编码路径到 `paths.py` 单文件
- [ ] 修复所有 `except Exception: pass`，至少 `log.debug` 一下
- [ ] 加 `from __future__ import annotations` 一致性

**风险：零**。语法/可读性整理。改完后重启机器人验证行为不变。

### Phase 2 — 模块切分（行为不变，1 天）
- [ ] 把 8 个 API 方法搬到 `trader/exchange/binance_futures.py`
- [ ] 把 `Position` 改成 `@dataclass`（用 `gridtrader/quant/risk.py` 里已有的）
- [ ] 把 `_load_pnl_state`/`_save_pnl_state`/`dump_state` 搬到 `trader/state.py`
- [ ] 把 `can_open_new`/`reset_daily`/`_check_streak` 搬到 `trader/risk.py`
- [ ] `LiveTrader` 改成 `Trader`，组合 4 个对象而不是继承所有职责

**风险：低**。每步搬完跑一次回测 + dry_run 验证。

### Phase 3 — 测试覆盖（1 天）
- [ ] `tests/test_risk.py` — killswitch / cooldown / daily cap 边界
- [ ] `tests/test_exchange.py` — 用 `responses` mock Binance HTTP
- [ ] `tests/test_state.py` — pnl 往返序列化
- [ ] `tests/test_trader_loop.py` — fake exchange + 真策略跑 1000 tick
- [ ] CI 配置（GitHub Actions，每 push 跑测试）

### Phase 4 — 配置统一（半天）
- [ ] 删除模块级全局，全部走 `TraderConfig` dataclass
- [ ] `.env` 只放凭证，YAML 只放策略/风控
- [ ] 启动时打印解析后的配置（便于排查）

### Phase 5 — 文档（半天）
- [ ] 重写 README：装→配→跑 三段，每段 5 行内
- [ ] `docs/architecture.md` 画一张数据流图
- [ ] `docs/runbook.md` 把"机器人挂了怎么办"步骤化

---

## 六、改完后的"轻松看懂"长什么样

新人 clone 完，README 三步走：

```bash
# 1. 装依赖
uv pip install -e .

# 2. 配置（复制模板填两个值）
cp .env.example .env && vim .env
cp config/trader.yaml.example config/trader.yaml

# 3. 跑（先 dry-run）
python -m trader --dry-run
```

代码逻辑就一张图：

```
   trader/__main__.py
        │
        ▼
   trader.Trader.run()
        │
   ┌────┴────┬────────────┬──────────┐
   ▼         ▼            ▼          ▼
Exchange  Strategy     Risk       State
(API)     (信号)       (风控)     (持久化)
```

每个文件 < 200 行，一个职责，独立可测。

---

## 七、什么时候动手

- 当前账户有持仓在跑，**不**建议立刻 refactor 主代码
- 推荐：今晚或明早机器人 FLAT 时启动 Phase 1（零行为变更）
- Phase 2 之后跑 24h dry_run 对照，确认逻辑一致再切线上

我可以按这个计划逐步执行，每个 Phase 完成后 commit + 推 GitHub + 写到 reports/，你随时验收。要现在开始 Phase 1 吗？
