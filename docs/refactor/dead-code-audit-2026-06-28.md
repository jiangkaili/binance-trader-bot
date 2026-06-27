# Dead-Code Audit — binance-trader-bot
**Date:** 2026-06-28  
**Scope:** Every `.py`, `.yaml`, `.json`, `.md`, `.sh`, `.txt` file in the repo  
**Method:** Read all 73 git-tracked Python files + config/scripts/docs; cross-referenced imports/usage with grep

---

## Executive Summary

The repo has **two parallel codebases** that share only the `gridtrader/quant/` library:

| Codebase | Entry point | Status |
|---|---|---|
| **Live trading bot** | `scripts/live_trader.py` (910 lines, monolith) | ✅ Production |
| **Modular refactor** | `trader/` package + `trader/__main__.py` | ⚠️ Incomplete (Phase 2, missing v7 features) |
| **51bitquant grid framework** | `main.py`, `main_futures_script.py`, `main_spot_script.py` | ❌ Dead — original fork code, not used by the bot |

**~36 files** (the entire `gridtrader/trader/`, `gridtrader/event/`, `gridtrader/gateway/`, `gridtrader/api/` packages + `resources/` images + 3 JSON configs + 3 root `main*.py` scripts + ~10 Chinese/Portuguese markdown docs) are the original 51bitquant framework and can be deleted outright.

**~8 more files** are test-only modules never used in production (`gridtrader/quant/risk.py`, parts of `gridtrader/quant/config.py`).

---

## 1. DEAD PACKAGES — Delete Entirely

### 1a. `gridtrader/trader/` — 51bitquant Grid Framework (14 .py files)

| File | Purpose | Used by live code? |
|---|---|---|
| `gridtrader/trader/__init__.py` | Package init (empty) | ❌ |
| `gridtrader/trader/engine.py` | `MainEngine`, `CtaEngine` — grid strategy engine | ❌ Only `main*.py` |
| `gridtrader/trader/constant.py` | Enums: Direction, OrderType, etc. | ❌ Only grid framework |
| `gridtrader/trader/event.py` | Event type constants | ❌ Only grid framework |
| `gridtrader/trader/gateway.py` | Base gateway interface | ❌ Only grid framework |
| `gridtrader/trader/object.py` | Data objects: TickData, BarData, OrderData, etc. | ❌ Only grid framework |
| `gridtrader/trader/setting.py` | `SETTINGS` dict loader for `vt_setting.json` | ❌ Only `main*.py` |
| `gridtrader/trader/utility.py` | Helper functions | ❌ Only grid framework |
| `gridtrader/trader/strategies/__init__.py` | Package init | ❌ |
| `gridtrader/trader/strategies/futures_grid_long_short_strategy.py` | Long-short grid strategy | ❌ |
| `gridtrader/trader/strategies/futures_grid_strategy.py` | Futures grid strategy | ❌ |
| `gridtrader/trader/strategies/spot_grid_strategy.py` | Spot grid strategy | ❌ |
| `gridtrader/trader/strategies/template.py` | Strategy template base class | ❌ |
| `gridtrader/trader/ui/__init__.py` | UI package init | ❌ |
| `gridtrader/trader/ui/ico/__init__.py` | Icon package init | ❌ |
| `gridtrader/trader/ui/mainwindow.py` | `MainWindow`, `create_qapp` — Qt GUI | ❌ Only `main.py` |
| `gridtrader/trader/ui/widget.py` | UI widgets | ❌ Only grid framework |

**Recommendation: DELETE** the entire `gridtrader/trader/` directory.

### 1b. `gridtrader/event/` — Event Engine (2 .py files)

| File | Used by live code? |
|---|---|
| `gridtrader/event/__init__.py` | ❌ Only grid framework |
| `gridtrader/event/engine.py` — `EventEngine` class | ❌ Only grid framework |

**Recommendation: DELETE**

### 1c. `gridtrader/gateway/` — Exchange Gateways (4 .py files)

| File | Used by live code? |
|---|---|
| `gridtrader/gateway/__init__.py` | ❌ |
| `gridtrader/gateway/binance/__init__.py` | ❌ |
| `gridtrader/gateway/binance/binance_futures_usdt_gateway.py` | ❌ Only grid framework |
| `gridtrader/gateway/binance/binance_spot_gateway.py` | ❌ Only grid framework |

**Recommendation: DELETE**

### 1d. `gridtrader/api/` — REST/WebSocket Clients (4 .py files)

| File | Used by live code? |
|---|---|
| `gridtrader/api/__init__.py` | ❌ |
| `gridtrader/api/rest/__init__.py` | ❌ |
| `gridtrader/api/rest/rest_client.py` | ❌ Only grid framework |
| `gridtrader/api/websocket/__init__.py` | ❌ |
| `gridtrader/api/websocket/websocket_client.py` | ❌ Only grid framework |

**Recommendation: DELETE**

### 1e. Root-Level Entry Scripts (3 .py files)

| File | Purpose | Used? |
|---|---|---|
| `main.py` | Launches Qt GUI for grid framework | ❌ Dead |
| `main_futures_script.py` | Runs futures grid strategy via `MainEngine` | ❌ Dead |
| `main_spot_script.py` | Runs spot grid strategy via `MainEngine` | ❌ Dead |

**Recommendation: DELETE**

### 1f. 51bitquant Config/Resource Files

| File | Purpose | Used? |
|---|---|---|
| `gridtrader/grid_strategy_data.json` | Grid strategy state | ❌ |
| `gridtrader/grid_strategy_setting.json` | Grid strategy settings | ❌ |
| `gridtrader/vt_setting.json` | Framework settings (gateways, RI, etc.) | ❌ |
| `resources/add_future_grid_strategy.png` | UI screenshot | ❌ |
| `resources/add_spot_grid_strategy.png` | UI screenshot | ❌ |
| `resources/connect_future_usdt.png` | UI screenshot | ❌ |
| `resources/connect_futures_coin.png` | UI screenshot | ❌ |
| `resources/connect_spot.png` | UI screenshot | ❌ |
| `resources/img_window.png` | UI screenshot | ❌ |
| `resources/start_strategy.png` | UI screenshot | ❌ |

**Recommendation: DELETE**

### 1g. 51bitquant Documentation (markdown files from fork)

| File | Recommendation |
|---|---|
| `README.md` | **REPLACE** — current README is 51bitquant's, not the bot's |
| `README-Chinese.md` | DELETE |
| `README-Portuguese.md` | DELETE |
| `使用手册.md` (User Manual) | DELETE |
| `免责声明.md` (Disclaimer) | DELETE |
| `安全策略.md` (Security Policy) | DELETE |
| `架构说明.md` (Architecture) | DELETE |
| `策略归档.md` (Strategy Archive) | DELETE |
| `视频教程.md` (Video Tutorial) | DELETE |
| `贡献指南.md` (Contribution Guide) | DELETE |
| `路线图.md` (Roadmap) | DELETE |
| `promotion/posts.md` | DELETE |
| `.github/ISSUE_TEMPLATE/*.md` | DELETE or REPLACE |

---

## 2. DEAD MODULES — Test-Only, Not Used in Production

### 2a. `gridtrader/quant/risk.py` — Quant RiskManager (10,318 chars)

**Used by:** `tests/test_backtest_risk.py` ONLY  
**NOT used by:** `scripts/live_trader.py` (uses its own inline risk logic), `trader/trader.py` (uses `trader/risk.py`), `gridtrader/quant/backtest.py` (has its own internal risk)

Contains `RiskManager`, `RiskViolation`, `Account`, `Position` classes — a full risk management system that was never wired into the live bot.

**Recommendation: DELETE** (or keep only if you plan to integrate it). The live bot uses `trader/risk.py` instead.

### 2b. `gridtrader/quant/config.py` — Mostly Dead (5,971 chars)

| Class/Function | Used by | Status |
|---|---|---|
| `RiskSettings` | `gridtrader/quant/risk.py` + tests | ⚠️ Only if risk.py is kept |
| `ApiSettings` | Nobody (internally by `QuantSettings`) | ❌ DEAD |
| `StorageSettings` | Nobody (internally by `QuantSettings`) | ❌ DEAD |
| `QuantSettings` | Nobody | ❌ DEAD |
| `_env_bool()`, `_env_float()`, `_env_int()` | Only by dead `QuantSettings.from_env()` | ❌ DEAD |
| `load_dotenv()` call at module level | Side effect on import | ⚠️ Unwanted side effect |

**Recommendation: CLEAN** — if you delete `gridtrader/quant/risk.py`, this entire file becomes dead. If you keep risk.py, strip everything except `RiskSettings`.

---

## 3. DEAD FUNCTIONS/METHODS

### 3a. `gridtrader/quant/storage.py` — Dead Store Methods

| Method | Defined | Called | Status |
|---|---|---|---|
| `Store.log_order()` | Line 121 | Never | ❌ DEAD |
| `Store.orders()` | Line ~140 | Never | ❌ DEAD |
| `Store.events()` | Line ~160 | Never | ❌ DEAD |
| `Store.log_trade()` | Line ~80 | ✅ live_trader.py, trader.py, tests | KEEP |
| `Store.log_event()` | Line ~100 | ✅ live_trader.py | KEEP |
| `Store.trades()` | Line ~180 | ✅ trader/trader.py, tests | KEEP |
| `Store.daily_pnl()` | Line 200 | ✅ gridtrader/quant/risk.py, tests | KEEP (if risk.py kept) |

**Recommendation: CLEAN** — delete `log_order()`, `orders()`, `events()` and their SQL table creation.

### 3b. `gridtrader/quant/indicators.py` — Functions Only Used by Tests

| Function | Used by production? | Used by tests? |
|---|---|---|
| `sma()` | ✅ internally by `bollinger()` | ✅ |
| `ema()` | ✅ live_trader.py (EMA200 filter), strategies | ✅ |
| `rsi()` | ✅ RsiRevertStrategy, live_trader.py | ✅ |
| `bollinger()` | ✅ BollingerStrategy | ✅ |
| `adx()` | ✅ live_trader.py (trend filter) | ✅ |
| `atr()` | ❌ Not used by any strategy or live code | ✅ test_indicators.py only |
| `macd()` | ❌ Not used by any strategy or live code | ✅ test_indicators.py only |
| `momentum()` | ✅ MomentumStrategy | ✅ |

**Recommendation: CLEAN** — `atr()` and `macd()` are test-only. Keep if you want the indicator library complete; delete if minimizing.

### 3c. `trader/exchange.py` — `place_algo_stop()` vs `place_exchange_stops()`

| Method | Used by |
|---|---|
| `place_algo_stop()` | `scripts/place_safety_stop.py` + tests |
| `place_exchange_stops()` | `trader/trader.py` |
| `get_open_orders()` | `scripts/check_open_orders.py`, `scripts/place_safety_stop.py` + tests |
| `get_open_algo_orders()` | `scripts/list_algo_orders.py`, `scripts/check_open_orders.py`, `scripts/place_safety_stop.py` + tests |
| `get_account_balance()` | `scripts/check_open_orders.py` + tests |
| `fetch_last_realized_pnl()` | `scripts/live_trader.py` |
| `maybe_resync_time()` | `trader/trader.py` |

**Note:** `place_algo_stop()` and `place_exchange_stops()` are nearly identical. `place_exchange_stops()` calls `place_algo_stop()` internally... actually no — looking at the code, `place_exchange_stops()` makes its own direct `_call()` to `/fapi/v1/algoOrder` without calling `place_algo_stop()`. This is **code duplication**. 

**Recommendation: CLEAN** — `place_exchange_stops()` should delegate to `place_algo_stop()`.

---

## 4. UNUSED IMPORTS

| File | Unused Import | 
|---|---|
| `gridtrader/quant/risk.py` | `field` (from `dataclasses`) |
| `gridtrader/quant/strategies.py` | `Optional` (from `typing`) |
| `gridtrader/quant/backtest.py` | `field` (from `dataclasses`), `Optional` (from `typing`) |
| `trader/config.py` | `field` (from `dataclasses`) |
| `scripts/sweep_multi.py` | `Optional` (from `typing`) |
| `tests/test_backtest_risk.py` | `os` |

**Recommendation: CLEAN** — remove all unused imports.

---

## 5. STALE / BROKEN TESTS

### 5a. `tests/test_trader_v2.py::test_config_loads_yaml()` — WILL FAIL

```python
# Test asserts (STALE — v2 era):
assert cfg.leverage == 10        # actual: 5
assert cfg.rsi_oversold == 12.0  # actual: 15.0
assert cfg.rsi_overbought == 88.0 # actual: 85.0
assert cfg.stop_loss_pct == 0.005 # actual: 0.015
assert cfg.take_profit_pct == 0.010 # actual: 0.030
```

The `config/trader.yaml` has been updated to v6/v7 parameters but the test still checks v2 values. **This test is broken.**

**Recommendation: FIX** — update assertions to match current config, or make the test load a fixture YAML.

### 5b. `tests/test_trader_v2.py::test_config_defaults_match_legacy()` — Tests Dead Defaults

Tests that `TraderConfig()` defaults match "legacy" values (leverage=20, rsi_oversold=20.0, etc.). These defaults are from v1/v2 and don't match the current production config. The test is correctly named ("legacy") but the defaults themselves are misleading.

**Recommendation: KEEP** (test is valid for documenting legacy defaults) but update `TraderConfig` defaults to match current production values if the modular refactor is meant to replace `live_trader.py`.

---

## 6. ORPHANED CONFIG KEYS in `config/trader.yaml`

| Key | Value | Read by any .py? |
|---|---|---|
| `streak_cooldown_hours` | `24` | ❌ Never read — live_trader.py hardcodes `24 * 3600` |
| `streak_loss_count` | `3` | ❌ Never read — live_trader.py hardcodes `3` |
| `warmup_bars` | `50` | ⚠️ Read by live_trader.py but value is misleading — EMA200 needs 200+ bars. Functionally OK because `get_klines(limit=300)` always returns 300 bars, so the warmup check always passes. |

**Recommendation: CLEAN** — either wire `streak_cooldown_hours` and `streak_loss_count` into the code, or remove them from the YAML. Update `warmup_bars` to `210` to match the EMA200 requirement.

---

## 7. CODE DUPLICATION

### 7a. Two Risk Managers
- `trader/risk.py` — `RiskManager` class (6,270 chars) — used by `trader/trader.py`
- `gridtrader/quant/risk.py` — `RiskManager` class (10,518 chars) — used by tests only

**Recommendation:** Pick one. The `trader/risk.py` version is simpler and production-used. The `gridtrader/quant/risk.py` version is more feature-rich but never wired in.

### 7b. Two Config Systems
- `trader/config.py` — `TraderConfig`, `RuntimeContext`, `load_env_file()` — used by live code
- `gridtrader/quant/config.py` — `RiskSettings`, `ApiSettings`, `QuantSettings` — used by tests only

**Recommendation:** Delete `gridtrader/quant/config.py` if you delete `gridtrader/quant/risk.py`.

### 7c. Two Position Classes
- `trader/models.py` — `Position` dataclass (1,801 chars)
- `gridtrader/quant/risk.py` — `Position` dataclass (inside risk.py)

**Recommendation:** Delete the one in `gridtrader/quant/risk.py` if you delete that module.

### 7d. Two Trading Bot Implementations
- `scripts/live_trader.py` — `LiveTrader` class (910 lines) — **PRODUCTION**
- `trader/trader.py` — `Trader` class (~340 lines) — **INCOMPLETE REFACTOR**

The `trader/` package is missing v7 features:
- ❌ No ADX trend filter (`TREND_FILTER_ENABLED`)
- ❌ No EMA200 trend-alignment filter (`TREND_EMA_FILTER_ENABLED`)
- ❌ No streak-loss cooldown
- ❌ `TraderConfig` doesn't have `trend_filter_*` or `trend_ema_*` fields
- ❌ `exchange.get_klines()` has `limit=100` but live_trader.py uses `limit=300`
- ❌ `TraderConfig` defaults are v1/v2 era (leverage=20, stop_loss_pct=0.01)

**Recommendation:** Either complete the `trader/` refactor (add v7 features) or delete it and keep `live_trader.py` as the single source of truth.

### 7e. Duplicated Indicator Code in Sweep Scripts

| Script | Duplicates | Instead of importing from |
|---|---|---|
| `sweep_sltp.py` | `rsi()`, `adx()` | `gridtrader.quant.indicators` |
| `sweep_multi.py` | `rsi()`, `adx()`, `ema()`, `bollinger()` | `gridtrader.quant.indicators` |
| `sweep_rsi_thresholds.py` | `compute_rsi()` | `gridtrader.quant.indicators` |

**Note:** The duplicated `rsi()` in sweep scripts uses a **different algorithm** (SMA-based) than `gridtrader.quant.indicators.rsi()` (Wilder's EWM-based). This means sweep results may not match live behavior exactly.

**Recommendation: CLEAN** — import from `gridtrader.quant.indicators` to ensure consistency.

### 7f. `scripts/live_trader.py` vs `trader/state.py` — Duplicated State Logic

`live_trader.py` has inline state save/load (lines ~640-680) that duplicates `trader/state.py`'s `save_state()`/`load_state()` functions.

**Recommendation:** Use `trader/state.py` in `live_trader.py` if the modular package is kept.

---

## 8. SWEEP SCRIPTS — Relevance Assessment

| Script | Tests current v7 strategy? | Uses shared indicators? | Recommendation |
|---|---|---|---|
| `sweep_sltp.py` | ⚠️ Partially (RSI 12/88 but no EMA200 filter) | ❌ Duplicates rsi/adx | KEEP but CLEAN |
| `sweep_multi.py` | ✅ Yes (includes `rsi_revert_ema` strategy type) | ❌ Duplicates rsi/adx/ema/bollinger | KEEP but CLEAN |
| `sweep_rsi_thresholds.py` | ⚠️ No (uses v3 params: 25 USDT margin, 10x lev) | ❌ Duplicates compute_rsi | ARCHIVE (superseded by sweep_multi) |
| `sweep_15m.py` | ❌ No (tests MA cross on 15m) | ✅ Uses `gridtrader.quant.backtest` | ARCHIVE |
| `sweep_all_15m.py` | ❌ No (tests multiple strategies on 15m) | ✅ Uses `gridtrader.quant.backtest` | ARCHIVE |
| `run_backtest.py` | ✅ General-purpose backtest runner | ✅ Uses `gridtrader.quant.backtest` | KEEP |
| `backtest_exit_logic.py` | ✅ Tests exit logic variants | ✅ Uses `gridtrader.quant.indicators` | KEEP |
| `fetch_klines.py` | N/A (data collection) | ✅ Uses `gridtrader.quant.hmac_client` | KEEP |

---

## 9. UTILITY SCRIPTS — Assessment

| Script | Purpose | Uses trader/ package? | Recommendation |
|---|---|---|---|
| `check_open_orders.py` | List positions + orders + balances | ✅ `trader.exchange.BinanceFutures` | KEEP |
| `list_algo_orders.py` | List algo (conditional) orders | ✅ `trader.exchange.BinanceFutures` | KEEP |
| `place_safety_stop.py` | Place emergency stop-loss | ✅ `trader.exchange.BinanceFutures` | KEEP |
| `trade_watchdog.py` | Monitor bot health | ❌ Hardcoded WSL paths, reads state JSON | CLEAN (fix hardcoded paths) |
| `ping.py` | Connectivity check | ❌ Standalone (own HTTP code) | KEEP (standalone is intentional) |
| `positions_futures.py` | List futures positions | ✅ `gridtrader.quant.hmac_client` | KEEP |
| `spot_account.py` | List spot balances | ✅ `gridtrader.quant.hmac_client` | KEEP |
| `transfer_to_futures.py` | Transfer USDT spot→futures | ✅ `gridtrader.quant.hmac_client` | KEEP |

### `trade_watchdog.py` Issues:
- Hardcoded WSL path: `DATA = "/mnt/c/Users/admin/binance_trader/data"`
- Uses PowerShell (`subprocess.run(["powershell.exe", ...])`) to check if bot process is alive
- These are environment-specific and will break on any other machine

**Recommendation: CLEAN** — make paths configurable via env vars or `config/trader.yaml`.

---

## 10. OTHER OBSOLETE ARTIFACTS

### 10a. `.env.example` Contains a Redacted Key
```
BINANCE_API_KEY=***
BINANCE_API_SECRET=your_a...here
```
The `***` and `your_a...here` are placeholders, not real secrets. **No action needed.**

### 10b. `requirements.txt` Includes Qt Dependencies
```
PyQt5
QtPy
```
These are only needed by `gridtrader/trader/ui/` (the 51bitquant GUI). If you delete the grid framework, remove these from requirements.

**Recommendation: CLEAN** — remove `PyQt5` and `QtPy` from requirements.txt after deleting grid framework.

### 10c. `gridtrader/__init__.py` Version String
```python
__version__ = "3.5"
```
This is the 51bitquant version, not the bot version. The bot version is in `trader/__init__.py` (`2.0.0-phase2`).

**Recommendation: UPDATE** — if you keep `gridtrader/` (just the `quant/` subpackage), update the version.

### 10d. Shell Scripts — Assessment

| Script | Purpose | Recommendation |
|---|---|---|
| `launch_daemon.sh` | Launch bot as daemon | KEEP |
| `run_trader.sh` | Run bot (foreground) | KEEP |
| `start_trader.sh` | Start bot with nohup | KEEP |
| `stop_trader.sh` | Stop bot via PID file | KEEP |
| `watchdog.sh` | Restart bot if it dies | KEEP |

All shell scripts reference `scripts/live_trader.py` as the entry point — consistent with production. **No issues found.**

---

## 11. SUMMARY OF RECOMMENDATIONS

### DELETE immediately (36+ files, zero risk):
- `gridtrader/trader/` (entire directory, 17 .py files)
- `gridtrader/event/` (entire directory, 2 .py files)
- `gridtrader/gateway/` (entire directory, 4 .py files)
- `gridtrader/api/` (entire directory, 5 .py files)
- `gridtrader/*.json` (3 config files)
- `resources/` (7 PNG screenshots)
- `main.py`, `main_futures_script.py`, `main_spot_script.py` (3 root scripts)
- 51bitquant markdown docs (11+ files)
- `.github/ISSUE_TEMPLATE/` (3 template files)

### DELETE if not integrating quant risk system (1 file):
- `gridtrader/quant/risk.py` (10,318 chars — only used by tests)

### CLEAN (remove dead code within kept files):
- `gridtrader/quant/storage.py` — delete `log_order()`, `orders()`, `events()` methods
- `gridtrader/quant/config.py` — delete `ApiSettings`, `StorageSettings`, `QuantSettings`, env helpers, `load_dotenv()` side effect (or delete entire file if risk.py is deleted)
- `gridtrader/quant/indicators.py` — optionally delete `atr()`, `macd()` (test-only)
- `trader/exchange.py` — make `place_exchange_stops()` delegate to `place_algo_stop()`
- All files with unused imports (see §4)

### FIX:
- `tests/test_trader_v2.py::test_config_loads_yaml()` — update assertions to match current config
- `config/trader.yaml` — remove orphaned `streak_cooldown_hours` / `streak_loss_count` keys (or wire them in); update `warmup_bars` to 210
- `scripts/trade_watchdog.py` — replace hardcoded WSL paths with configurable paths
- `requirements.txt` — remove `PyQt5`, `QtPy` after grid framework deletion

### DECIDE:
- **`trader/` package vs `scripts/live_trader.py`** — the modular refactor (`trader/`) is missing v7 features (ADX filter, EMA200 filter, streak cooldown). Either complete it or delete it. Keeping both creates confusion and maintenance burden.
- **Sweep scripts with duplicated indicators** — `sweep_sltp.py`, `sweep_multi.py`, `sweep_rsi_thresholds.py` duplicate indicator code with different algorithms. Import from `gridtrader.quant.indicators` instead.

### File Count Impact:
- **Before:** 73 git-tracked .py files
- **After DELETE:** ~33 .py files (40 deleted)
- **After CLEAN:** ~33 .py files (same count, less code within)
