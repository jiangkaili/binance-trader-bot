#!/usr/bin/env python3
"""Generate the daily review report from real data.

Produces `reports/YYYY/MM/YYYY-MM-DD.md` following the 6-section structure
documented in `reports/README.md`:

  1. 当前策略参数      (config/trader.yaml + diff vs the previous report)
  2. 运行状态          (data/live_trader.state, data/live_trader.log, events)
  3. 账户概况          (Binance Futures API — live, read-only)
  4. 成交统计          (data/trades.db)
  5. 策略分析          (derived from the above)
  6. 建议              (rule-based, priority-tagged)

Everything is re-derivable: the report prints the SQL it used, so anyone can
clone the repo and reproduce every number.

Usage
-----
    # today's report (local date)
    python scripts/daily_report.py

    # a specific day (backfill)
    python scripts/daily_report.py --date 2026-11-22

    # skip the network (no account snapshot — useful offline)
    python scripts/daily_report.py --no-api

    # print to stdout instead of writing a file
    python scripts/daily_report.py --print

    # overwrite an existing report
    python scripts/daily_report.py --force
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.hmac_client import signed_request
from trader.config import HOSTS, load_env_file
from trader.paths import (
    COOLDOWN_PATH, DATA_DIR, KILLSWITCH_PATH, LOG_PATH,
    PROJECT_ROOT, STATE_PATH, TRADES_DB_PATH, CONFIG_PATH,
)

REPORTS_DIR = PROJECT_ROOT / "reports"
CONFIG_HISTORY = REPORTS_DIR / ".config_history.json"

# Params surfaced in the report, in display order. Anything else in the YAML is
# still recorded in the history file, it just does not get its own table row.
PARAM_LABELS = [
    ("symbol", "symbol", "交易对"),
    ("target_position_usdt", "target_position_usdt", "目标保证金 USDT"),
    ("leverage", "leverage", "杠杆"),
    ("strategy_name", "strategy_name", "策略"),
    ("rsi_period", "rsi_period", "RSI 周期"),
    ("rsi_oversold", "rsi_oversold", "RSI 超卖（做多）"),
    ("rsi_overbought", "rsi_overbought", "RSI 超买（做空）"),
    ("kline_interval", "kline_interval", "K 线"),
    ("poll_seconds", "poll_seconds", "轮询秒"),
    ("warmup_bars", "warmup_bars", "预热 K 线"),
    ("cooldown_bars_after_trade", "cooldown_bars_after_trade", "平仓后冷却(根)"),
    ("stop_loss_pct", "stop_loss_pct", "止损"),
    ("take_profit_pct", "take_profit_pct", "止盈"),
    ("disable_signal_exit", "disable_signal_exit", "禁用信号平仓"),
    ("daily_loss_pct", "daily_loss_pct", "日亏上限"),
    ("weekly_loss_pct", "weekly_loss_pct", "周亏上限"),
    ("trend_filter_enabled", "trend_filter_enabled", "ADX 趋势过滤"),
    ("trend_filter_adx_threshold", "trend_filter_adx_threshold", "ADX 阈值"),
    ("trend_ema_filter_enabled", "trend_ema_filter_enabled", "EMA 对齐过滤"),
    ("trend_ema_period", "trend_ema_period", "EMA 周期"),
    ("funding_rate_enabled", "funding_rate_enabled", "资金费率信号"),
    ("funding_zscore_period", "funding_zscore_period", "费率 z 周期"),
    ("funding_zscore_threshold", "funding_zscore_threshold", "费率共振阈值"),
    ("funding_zscore_extreme", "funding_zscore_extreme", "费率独立阈值"),
    ("llm_enabled", "llm_enabled", "LLM 市场分析"),
    ("llm_interval_ticks", "llm_interval_ticks", "LLM 间隔(tick)"),
    ("llm_can_override_adx", "llm_can_override_adx", "LLM 可覆盖 ADX"),
    ("llm_min_confidence", "llm_min_confidence", "LLM 最低置信"),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def local_tz():
    """The machine's local timezone (report dates are local dates)."""
    return datetime.now().astimezone().tzinfo


def parse_ts(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp from the DB/log into local time."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz())


def local_day_utc_window(report_date: date) -> tuple[datetime, datetime]:
    """[start, end) of the report's LOCAL day, expressed in UTC.

    The DB stores UTC ISO strings while reports are dated locally, so the
    reproducibility SQL must use an explicit UTC window — `date(ts)` would
    silently use the UTC date and mis-bucket every evening fill.
    """
    tz = local_tz()
    start = datetime(report_date.year, report_date.month, report_date.day, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def fmt_pct(v) -> str:
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_num(v, digits: int = 4) -> str:
    try:
        return f"{float(v):+.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config_history() -> dict:
    if not CONFIG_HISTORY.exists():
        return {}
    try:
        return json.loads(CONFIG_HISTORY.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config_history(history: dict) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_HISTORY.write_text(
            json.dumps(history, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"WARN: could not save config history: {e}", file=sys.stderr)


def previous_config(history: dict, report_date: date) -> tuple[str | None, dict | None]:
    """Most recent recorded config strictly before the report date."""
    best_key, best_cfg = None, None
    for key, cfg in history.items():
        try:
            d = date.fromisoformat(key)
        except ValueError:
            continue
        if d < report_date and (best_key is None or d > date.fromisoformat(best_key)):
            best_key, best_cfg = key, cfg
    return best_key, best_cfg


# --------------------------------------------------------------------------
# data sources
# --------------------------------------------------------------------------

def read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def state_age_seconds() -> float | None:
    if not STATE_PATH.exists():
        return None
    return (datetime.now() - datetime.fromtimestamp(STATE_PATH.stat().st_mtime)).total_seconds()


def scan_log(report_date: date) -> dict:
    """Pull today's lines out of the live_trader log."""
    out = {
        "exists": LOG_PATH.exists(),
        "size": LOG_PATH.stat().st_size if LOG_PATH.exists() else 0,
        "mtime": None,
        "last_heartbeat": None,
        "starts": [],
        "action_lines": [],
        "counts": {"INFO": 0, "WARN": 0, "ACTION": 0, "ERROR": 0, "CRITICAL": 0},
        "total_lines": 0,
    }
    if not LOG_PATH.exists():
        return out
    out["mtime"] = datetime.fromtimestamp(LOG_PATH.stat().st_mtime).astimezone(local_tz())
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    out["total_lines"] = len(lines)
    for line in lines:
        if not line.startswith("["):
            continue
        head = line.split("]", 1)[0].lstrip("[")
        dt = parse_ts(head)
        if dt is None or dt.date() != report_date:
            continue
        for lvl in ("CRITICAL", "ERROR", "WARN", "ACTION", "INFO"):
            if f"[{lvl}]" in line:
                out["counts"][lvl] += 1
                break
        if "heartbeat tick=" in line:
            out["last_heartbeat"] = line
        if "] STARTED " in line:
            out["starts"].append(line)
        if "[ACTION]" in line or "[CRITICAL]" in line or "[ERROR]" in line:
            out["action_lines"].append(line)
    return out


def db_trades(db_path: Path = TRADES_DB_PATH) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, ts, symbol, side, price, qty, fee, strategy, order_id, source, pnl "
            "FROM trades ORDER BY ts"
        ).fetchall()
        c.close()
    except sqlite3.Error as e:
        print(f"WARN: could not read {db_path}: {e}", file=sys.stderr)
        return []
    out = []
    for r in rows:
        d = dict(r)
        d["dt"] = parse_ts(d.get("ts"))
        out.append(d)
    return out


def db_events(report_date: date, db_path: Path = TRADES_DB_PATH) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT ts, level, msg FROM events ORDER BY id").fetchall()
        c.close()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        d = dict(r)
        d["dt"] = parse_ts(d.get("ts"))
        if d["dt"] and d["dt"].date() == report_date:
            out.append(d)
    return out


def fetch_account(env_file: str) -> dict:
    """Read-only mainnet/testnet account snapshot. Never places an order."""
    result = {"ok": False, "error": None, "mode": None, "base": None,
              "account": None, "position": None,
              "open_orders": None, "algo_orders": None, "ticker": None}
    load_env_file(env_file)
    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
    base = HOSTS["testnet" if use_testnet else "prod"]
    result["mode"] = "testnet" if use_testnet else "mainnet"
    result["base"] = base
    if not key or not secret:
        result["error"] = "BINANCE_API_KEY / BINANCE_API_SECRET 未设置"
        return result
    try:
        r = requests.get(base + "/fapi/v1/time", timeout=10)
        r.raise_for_status()
        offset = int(r.json()["serverTime"]) - int(datetime.now().timestamp() * 1000)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"无法连接 Binance: {type(e).__name__}: {e}"
        return result
    try:
        ra = signed_request("GET", base + "/fapi/v2/account", {}, key, secret,
                            time_offset_ms=offset, timeout=15)
        if ra.status_code != 200:
            result["error"] = f"账户接口 HTTP {ra.status_code}: {ra.text[:160]}"
            return result
        result["account"] = ra.json()
    except Exception as e:  # noqa: BLE001
        result["error"] = f"账户请求失败: {type(e).__name__}: {e}"
        return result

    symbol = (load_config().get("symbol") or "BTCUSDT")
    try:
        rp = signed_request("GET", base + "/fapi/v2/positionRisk", {"symbol": symbol},
                            key, secret, time_offset_ms=offset, timeout=15)
        if rp.status_code == 200:
            for p in rp.json():
                if abs(float(p.get("positionAmt", 0) or 0)) > 1e-9:
                    result["position"] = p
                    break
            result["position_flat"] = result["position"] is None
    except Exception as e:  # noqa: BLE001
        result["position_error"] = f"{type(e).__name__}: {e}"

    for field, path in (("open_orders", "/fapi/v1/openOrders"),
                        ("algo_orders", "/fapi/v1/openAlgoOrders")):
        try:
            ro = signed_request("GET", base + path, {"symbol": symbol}, key, secret,
                                time_offset_ms=offset, timeout=15)
            if ro.status_code == 200:
                data = ro.json()
                if isinstance(data, dict):
                    data = data.get("data") or data.get("orders") or []
                result[field] = data
        except Exception:  # noqa: BLE001
            pass

    try:
        t = requests.get(base + "/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=10)
        if t.status_code == 200:
            result["ticker"] = float(t.json()["price"])
    except Exception:  # noqa: BLE001
        pass

    result["ok"] = True
    return result


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def trade_stats(rows: list[dict]) -> dict:
    closed = [r for r in rows if r.get("pnl") is not None]
    wins = [r for r in closed if (r["pnl"] or 0) > 0]
    losses = [r for r in closed if (r["pnl"] or 0) < 0]
    gross_profit = sum(r["pnl"] for r in wins) if wins else 0.0
    gross_loss = sum(r["pnl"] for r in losses) if losses else 0.0
    total = sum(r["pnl"] or 0 for r in closed)
    fees = sum(r.get("fee") or 0 for r in closed)
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    pf = (gross_profit / abs(gross_loss)) if gross_loss else (float("inf") if gross_profit else 0.0)
    return {
        "n": len(closed), "wins": len(wins), "losses": len(losses),
        "flat": len(closed) - len(wins) - len(losses),
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "total": total, "fees": fees,
        "avg_win": avg_win, "avg_loss": avg_loss, "pf": pf,
        "expectancy": (total / len(closed)) if closed else 0.0,
    }


def exit_reason_counts(action_lines: list[str]) -> dict:
    counts: dict[str, int] = {}
    for line in action_lines:
        marker = "reason="
        if "CLOSE " in line and marker in line:
            reason = line.split(marker, 1)[1].split()[0].split("(")[0].strip()
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def build_report(report_date: date, cfg: dict, state: dict | None, log: dict,
                 today_rows: list[dict], all_rows: list[dict], events: list[dict],
                 acct: dict, prev_date: str | None, prev_cfg: dict | None) -> str:
    L: list[str] = []
    add = L.append
    tzname = str(local_tz())
    now_local = datetime.now().astimezone()

    today_stats = trade_stats(today_rows)
    all_stats = trade_stats(all_rows)

    add(f"# 量化交易机器人复盘 — {report_date.isoformat()}")
    add("")
    add(f"生成时间：{now_local.strftime('%Y-%m-%dT%H:%M:%S%z')}（{tzname}）")
    add("")
    add("> 本报告由 `scripts/daily_report.py` 从 `data/trades.db`、`data/live_trader.state`、"
        "`data/live_trader.log` 与 Binance API 自动生成，全部数字可复现（见文末 SQL）。")
    add("")

    # ---------------- headline ----------------
    headlines = []
    if today_stats["n"] == 0:
        headlines.append("今日**零成交**")
    else:
        headlines.append(
            f"今日成交 **{today_stats['n']} 笔**，"
            f"胜率 {today_stats['win_rate']*100:.1f}%，"
            f"净盈亏 **{today_stats['total']:+.4f} USDT**"
        )
    if acct.get("ok") and acct.get("account"):
        eq = float(acct["account"].get("totalWalletBalance", 0) or 0)
        headlines.append(f"账户余额 **{eq:.4f} USDT**（API 实查）")
    errs = log["counts"]["ERROR"] + log["counts"]["CRITICAL"]
    headlines.append(f"今日错误 **{errs} 条**")
    add("**速览**：" + "，".join(headlines) + "。")
    add("")
    add("---")
    add("")

    # ---------------- 1. params ----------------
    add("## 【1】当前策略参数")
    add("")
    add("| 参数 | 当前值 | 说明 |")
    add("|---|---:|---|")
    for key, _, label in PARAM_LABELS:
        if key not in cfg:
            continue
        v = cfg[key]
        if isinstance(v, float) and key in ("stop_loss_pct", "take_profit_pct",
                                            "daily_loss_pct", "weekly_loss_pct"):
            shown = fmt_pct(v)
        elif isinstance(v, bool):
            shown = "true" if v else "false"
        else:
            shown = str(v)
        add(f"| `{key}` | {shown} | {label} |")
    add("")

    if prev_cfg is None:
        add("**与上一份报告的差异**：历史中没有更早的配置记录（首次生成），无对比。")
    else:
        diffs = []
        for key in sorted(set(cfg) | set(prev_cfg)):
            if key not in PARAM_LABELS_BY_KEY:
                continue
            a, b = prev_cfg.get(key), cfg.get(key)
            if a != b:
                diffs.append(f"`{key}`: **{a} → {b}**")
        if diffs:
            add(f"**与上一份报告（{prev_date}）的差异**：")
            add("")
            for d in diffs:
                add(f"- {d}")
        else:
            add(f"**与上一份报告（{prev_date}）的差异**：无变更。")
    add("")
    add("---")
    add("")

    # ---------------- 2. running state ----------------
    add("## 【2】运行状态")
    add("")
    add("| 项目 | 状态 |")
    add("|---|---|")
    age = state_age_seconds()
    if state is None:
        add("| state 文件 | ❌ 不存在，机器人可能从未启动 |")
    else:
        sdt = parse_ts(state.get("ts", ""))
        freshness = ("✅ 正常" if age is not None and age < 180
                     else f"⚠️ 已过期 {age:.0f}s（轮询间隔 60s）" if age is not None else "—")
        add(f"| state 更新时间 | {sdt.strftime('%Y-%m-%d %H:%M:%S') if sdt else '—'} "
            f"（距今 {age:.0f}s，{freshness}） |" if age is not None
            else f"| state 更新时间 | {sdt} |")
        add(f"| 当前 tick | {state.get('tick')} |")
        add(f"| 运行模式 | {'DRY-RUN（纸面）' if state.get('dry_run') else '**LIVE（实盘）**'} |")
        add(f"| 起始权益 | {state.get('starting_equity')} USDT |")
        add(f"| 当日 PnL | {fmt_num(state.get('daily_pnl'))} USDT |")
        add(f"| 当周 PnL | {fmt_num(state.get('weekly_pnl'))} USDT |")
        pos = state.get("position")
        if pos:
            add(f"| 当前持仓 | {pos.get('side')} qty={pos.get('qty')} "
                f"entry={pos.get('entry')} mark={pos.get('mark')} uPnl={fmt_num(pos.get('uPnl'))} |")
        else:
            add("| 当前持仓 | 空仓 |")
    add(f"| 最后心跳 | {log['last_heartbeat'] or '（今日无心跳）'} |")
    add(f"| 今日启动次数 | {len(log['starts'])} 次 |")
    add(f"| 今日日志计数 | INFO {log['counts']['INFO']} / ACTION {log['counts']['ACTION']} / "
        f"WARN {log['counts']['WARN']} / ERROR {log['counts']['ERROR']} / "
        f"CRITICAL {log['counts']['CRITICAL']} |")
    if events:
        ev_counts: dict[str, int] = {}
        for e in events:
            lvl = "WARN" if e["level"] in ("WARN", "WARNING") else e["level"]
            ev_counts[lvl] = ev_counts.get(lvl, 0) + 1
        add(f"| 今日事件（events 表） | {len(events)} 条："
            + " / ".join(f"{k} {v}" for k, v in sorted(ev_counts.items())) + " |")
    add(f"| 日志文件 | {log['size']} 字节，共 {log['total_lines']} 行 |")
    add(f"| KILLSWITCH | {'🛑 存在（禁止开仓）' if KILLSWITCH_PATH.exists() else '不存在'} |")
    add(f"| 连亏冷却 | {'⏸️ 存在' if COOLDOWN_PATH.exists() else '无'} |")
    add("")
    if log["starts"]:
        add("### 今日启动记录")
        add("")
        for s in log["starts"]:
            add(f"- `{s[:150]}`")
        add("")
    add("---")
    add("")

    # ---------------- 3. account ----------------
    add("## 【3】账户概况（Binance API 实查）")
    add("")
    if not acct.get("ok"):
        add(f"⚠️ 未能获取账户信息：{acct.get('error')}")
        add("")
    else:
        a = acct["account"] or {}
        add(f"环境：**{acct['mode']}**（`{acct['base']}`）")
        add("")
        add("| 项目 | 值 |")
        add("|---|---:|")
        add(f"| 钱包余额 | {float(a.get('totalWalletBalance', 0)):.4f} USDT |")
        add(f"| 可用余额 | {float(a.get('availableBalance', 0)):.4f} USDT |")
        add(f"| 未实现盈亏 | {float(a.get('totalUnrealizedProfit', 0)):+.4f} USDT |")
        add(f"| canTrade | {a.get('canTrade')} |")
        add(f"| 手续费等级 | {a.get('feeTier')} |")
        if acct.get("ticker"):
            add(f"| 标记价格 | {acct['ticker']:,.2f} |")
        add("")
        assets = [x for x in (a.get("assets") or []) if abs(float(x.get("walletBalance", 0) or 0)) > 1e-9]
        if assets:
            add("**非零资产**")
            add("")
            add("| 资产 | 钱包 | 可用 |")
            add("|---|---:|---:|")
            for x in assets:
                add(f"| {x['asset']} | {x['walletBalance']} | {x['availableBalance']} |")
            add("")
        add("**持仓 / 挂单**")
        add("")
        pos = acct.get("position")
        if pos:
            add(f"- 持仓：{pos.get('positionSide')} amt={pos.get('positionAmt')} "
                f"entry={pos.get('entryPrice')} mark={pos.get('markPrice')} "
                f"uPnl={pos.get('unRealizedProfit')} lev={pos.get('leverage')}x")
        else:
            add("- 持仓：空仓")
        oo, ao = acct.get("open_orders"), acct.get("algo_orders")
        if oo is not None:
            add(f"- 普通挂单：{len(oo)} 张")
        if ao is not None:
            add(f"- 条件单（止损/止盈）：{len(ao)} 张")
            for o in ao[:6]:
                add(f"  - {o.get('orderType') or o.get('type')} side={o.get('side')} "
                    f"trigger={o.get('triggerPrice') or o.get('stopPrice')}")
        add("")
    add("---")
    add("")

    # ---------------- 4. trades ----------------
    add("## 【4】成交统计")
    add("")
    add(f"### 今日（{report_date.isoformat()}）")
    add("")
    if today_stats["n"] == 0:
        add("今日无平仓成交。")
    else:
        add("| 指标 | 值 |")
        add("|---|---:|")
        add(f"| 平仓笔数 | {today_stats['n']} |")
        add(f"| 盈利 / 亏损 | {today_stats['wins']} / {today_stats['losses']} |")
        add(f"| 胜率 | {today_stats['win_rate']*100:.1f}% |")
        add(f"| 净盈亏 | {today_stats['total']:+.4f} USDT |")
        add(f"| 毛盈利 / 毛亏损 | {today_stats['gross_profit']:+.4f} / {today_stats['gross_loss']:+.4f} |")
        pf = today_stats["pf"]
        add(f"| Profit Factor | {'∞' if pf == float('inf') else f'{pf:.2f}'} |")
        add(f"| 平均盈利 / 平均亏损 | {today_stats['avg_win']:+.4f} / {today_stats['avg_loss']:+.4f} |")
        add(f"| 单笔期望 | {today_stats['expectancy']:+.4f} USDT |")
        add("")
        add("**逐笔明细**")
        add("")
        add("| # | 时间(本地) | 方向 | 价格 | 数量 | 盈亏 | 来源 |")
        add("|---|---|---|---:|---:|---:|---|")
        for r in today_rows:
            dt = r.get("dt")
            add(f"| {r['id']} | {dt.strftime('%H:%M:%S') if dt else '—'} | {r['side']} | "
                f"{r['price']:,.2f} | {r['qty']} | {fmt_num(r['pnl'])} | {r['source']} |")
    add("")
    add("### 累计")
    add("")
    add("| 指标 | 值 |")
    add("|---|---:|")
    add(f"| 累计平仓笔数 | {all_stats['n']} |")
    if all_stats["n"]:
        add(f"| 累计胜率 | {all_stats['win_rate']*100:.1f}% |")
        add(f"| 累计净盈亏 | {all_stats['total']:+.4f} USDT |")
        add(f"| 累计手续费 | {all_stats['fees']:.4f} USDT |")
        pf_all = all_stats["pf"]
        add(f"| 累计 Profit Factor | {'∞' if pf_all == float('inf') else f'{pf_all:.2f}'} |")
    else:
        add("| 累计胜率 / 净盈亏 / PF | —（尚无平仓成交） |")
    add("")
    reasons = exit_reason_counts(log["action_lines"])
    if reasons:
        closed_n = sum(reasons.values())
        add("**今日日志中的平仓动作**：" + "、".join(f"`{k}` ×{v}" for k, v in sorted(reasons.items())))
        if closed_n != today_stats["n"]:
            add("")
            add(f"> 注：日志里 {closed_n} 次平仓动作，数据库今日只有 {today_stats['n']} 笔记录。"
                "差异来自纸面（dry-run）测试成交或已被清理/回填的记录——日志口径比数据库更宽，"
                "以上表数据库口径为准。")
        add("")
    add("---")
    add("")

    # ---------------- 5. analysis ----------------
    add("## 【5】策略分析")
    add("")
    bullets: list[str] = []
    if state:
        rsi = state.get("rsi")
        adx = state.get("adx")
        fz = state.get("funding_z")
        lo, hi = float(cfg.get("rsi_oversold", 20)), float(cfg.get("rsi_overbought", 80))
        if isinstance(rsi, (int, float)) and rsi:
            dist_lo = rsi - lo
            dist_hi = hi - rsi
            if dist_lo <= 0:
                bullets.append(f"RSI={rsi:.1f} 已低于超卖阈值 {lo}，做多信号成立。")
            elif dist_hi <= 0:
                bullets.append(f"RSI={rsi:.1f} 已高于超买阈值 {hi}，做空信号成立。")
            else:
                bullets.append(
                    f"RSI={rsi:.1f}，距做多阈值 {lo} 还有 {dist_lo:.1f}，"
                    f"距做空阈值 {hi} 还有 {dist_hi:.1f}——处于中性区，无信号。"
                )
        if isinstance(adx, (int, float)) and adx and cfg.get("trend_filter_enabled"):
            th = float(cfg.get("trend_filter_adx_threshold", 25))
            if adx > th:
                bullets.append(f"ADX={adx:.1f} > {th}，趋势过滤正在**阻止**新开仓（即使 RSI 触极值也不进场）。")
            else:
                bullets.append(f"ADX={adx:.1f} ≤ {th}，趋势过滤放行，允许新开仓。")
        if isinstance(fz, (int, float)) and cfg.get("funding_rate_enabled"):
            th = float(cfg.get("funding_zscore_threshold", 2))
            ext = float(cfg.get("funding_zscore_extreme", 3))
            if abs(fz) >= ext:
                bullets.append(f"资金费率 z={fz:.2f} 已达独立信号阈值 ±{ext}。")
            elif abs(fz) >= th:
                bullets.append(f"资金费率 z={fz:.2f} 超过共振阈值 ±{th}，会过滤掉方向不一致的 RSI 信号。")
            else:
                bullets.append(f"资金费率 z={fz:.2f}，在 ±{th} 内，仅作共振过滤不产生独立信号。")
        if state.get("llm") is None and cfg.get("llm_enabled"):
            bullets.append("配置里 `llm_enabled: true`，但环境变量缺少 `LLM_API_URL/LLM_API_KEY/LLM_MODEL`，"
                           "LLM 层已被机器人自动禁用，决策完全由 RSI + ADX + 资金费率的确定性规则产生。")

    if today_stats["n"] == 0:
        bullets.append("今日零成交。零成交本身不是故障——RSI(7) 20/80 是极值策略，"
                       "平静行情下连续多日不开仓属于预期行为。")
    else:
        if today_stats["n"] < 20:
            bullets.append(f"今日 {today_stats['n']} 笔样本过小，任何胜率/PF 读数都没有统计意义。")
        if today_stats["pf"] != float("inf") and today_stats["pf"] < 1:
            bullets.append(f"今日 PF {today_stats['pf']:.2f} < 1，当日为负期望。")
        if reasons:
            sl = reasons.get("stop_loss", 0)
            tp = reasons.get("take_profit", 0)
            if sl or tp:
                bullets.append(f"平仓出口：止损 {sl} 次、止盈 {tp} 次"
                               f"（配置 R:R = 1:{float(cfg.get('take_profit_pct', 0)) / max(float(cfg.get('stop_loss_pct', 1e-9)), 1e-9):.1f}）。")

    if all_stats["n"] and all_stats["n"] < 100:
        bullets.append(f"累计仅 {all_stats['n']} 笔平仓，远低于「OOS ≥100 笔才有统计意义」的门槛"
                       "（见 `research/engineering/autonomous-iteration.md`），"
                       "当前任何参数调整都是在噪声上做决定。")

    if acct.get("ok") and acct.get("account") and cfg.get("target_position_usdt"):
        avail = float(acct["account"].get("availableBalance", 0) or 0)
        need = float(cfg["target_position_usdt"])
        if avail < need:
            bullets.append(f"可用余额 {avail:.2f} USDT 低于单笔目标保证金 {need:.2f} USDT，"
                           "开仓会因保证金不足失败。")
        else:
            worst = need * float(cfg.get("leverage", 5)) * float(cfg.get("stop_loss_pct", 0.015))
            fee = need * float(cfg.get("leverage", 5)) * 0.0008
            bullets.append(f"单笔最坏情况约 -{worst + fee:.2f} USDT（止损 + 双边手续费），"
                           f"可用余额 {avail:.2f} USDT 可承受约 {int(avail / (worst + fee)) if worst + fee else 0} 次连续止损。")

    if age is not None and age > 300:
        bullets.append(f"state 文件已 {age:.0f}s 未更新，机器人可能已停止——请检查进程。"
                       "（本报告在电脑关机后运行，state 过期是正常的。）")

    for b in bullets:
        add(f"- {b}")
    add("")
    add("---")
    add("")

    # ---------------- 6. recommendations ----------------
    add("## 【6】建议")
    add("")
    recs: list[tuple[str, str]] = []
    errs = log["counts"]["ERROR"] + log["counts"]["CRITICAL"]
    if errs:
        recs.append(("P0", f"今日有 {errs} 条 ERROR/CRITICAL，先看日志再谈策略。"))
    if KILLSWITCH_PATH.exists():
        recs.append(("P0", "`data/KILLSWITCH` 存在，机器人拒绝开新仓。确认无误后删除该文件才会恢复。"))
    if COOLDOWN_PATH.exists():
        recs.append(("P1", "`data/COOLDOWN_UNTIL` 存在——连续亏损触发的冷却期，到期自动解除。"))
    if all_stats["n"] < 100:
        recs.append(("P1", f"累计 {all_stats['n']} 笔 < 100 笔，**不建议**调参。先让样本长出来。"))
    if all_stats["n"] and all_stats["pf"] != float("inf") and all_stats["pf"] < 1:
        recs.append(("P1", f"累计 PF {all_stats['pf']:.2f} < 1。若样本达到 100 笔仍如此，"
                           "应回到 `scripts/backtest_exit_logic.py` 重新做窗口回测，而不是直接改参数。"))
    if acct.get("ok") and acct.get("account") and cfg.get("target_position_usdt"):
        avail = float(acct["account"].get("availableBalance", 0) or 0)
        need = float(cfg["target_position_usdt"])
        if avail < need * 1.1:
            recs.append(("P0", f"可用余额 {avail:.2f} USDT 不足以保证 {need} USDT 保证金 + 缓冲，"
                               "要么划转 USDT，要么调低 `target_position_usdt`。"))
    if cfg.get("llm_enabled") and state and state.get("llm") is None:
        recs.append(("P2", "`llm_enabled: true` 但 LLM 凭据缺失。要么补 `LLM_API_URL/KEY/MODEL`，"
                           "要么把 `llm_enabled` 改成 false 让配置与实际行为一致。"))
    if not recs:
        recs.append(("—", "无异常，保持当前参数不动。"))
    add("| 优先级 | 建议 |")
    add("|---|---|")
    for p, text in recs:
        add(f"| {p} | {text} |")
    add("")
    add("---")
    add("")

    # ---------------- appendix ----------------
    add("## 附：复现方法")
    add("")
    win_lo, win_hi = local_day_utc_window(report_date)
    lo_s = win_lo.strftime("%Y-%m-%dT%H:%M:%S")
    hi_s = win_hi.strftime("%Y-%m-%dT%H:%M:%S")
    add(f"报告日期是**本地日期**（{tzname}），而 `trades.ts` / `events.ts` 存的是 UTC。"
        f"所以下面的窗口是本地这一天对应的 UTC 区间：`{lo_s}Z` ~ `{hi_s}Z`。")
    add("")
    add("```sql")
    add("-- 今日平仓明细")
    add("SELECT id, ts, side, price, qty, pnl, source, order_id")
    add("FROM trades")
    add(f"WHERE ts >= '{lo_s}' AND ts < '{hi_s}' ORDER BY ts;")
    add("")
    add("-- 今日汇总")
    add("SELECT COUNT(*) AS n,")
    add("       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,")
    add("       ROUND(SUM(pnl), 4) AS net_pnl")
    add(f"FROM trades WHERE ts >= '{lo_s}' AND ts < '{hi_s}';")
    add("")
    add("-- 累计已实现盈亏（排除回填行）")
    add("SELECT COALESCE(SUM(pnl), 0) FROM trades")
    add("WHERE order_id IS NULL OR order_id NOT LIKE 'backfilled_%';")
    add("")
    add("-- 今日事件")
    add("SELECT ts, level, msg FROM events")
    add(f"WHERE ts >= '{lo_s}' AND ts < '{hi_s}' ORDER BY id;")
    add("```")
    add("")
    add("```bash")
    add("python scripts/daily_report.py --date %s" % report_date.isoformat())
    add("```")
    add("")
    return "\n".join(L)


PARAM_LABELS_BY_KEY = {k: label for k, _, label in PARAM_LABELS}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Generate the daily review report.")
    p.add_argument("--date", default=None, help="report date YYYY-MM-DD (default: today, local)")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--db", default=str(TRADES_DB_PATH),
                   help="trades sqlite path (default: data/trades.db)")
    p.add_argument("--no-api", action="store_true", help="skip the Binance account snapshot")
    p.add_argument("--print", dest="to_stdout", action="store_true", help="print instead of writing")
    p.add_argument("--force", action="store_true", help="overwrite an existing report")
    args = p.parse_args()

    if args.date:
        try:
            report_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: --date 需要 YYYY-MM-DD，收到 {args.date!r}", file=sys.stderr)
            return 2
    else:
        report_date = datetime.now().astimezone().date()

    cfg = load_config()
    state = read_state()
    log = scan_log(report_date)
    db_path = Path(args.db)
    all_rows = db_trades(db_path)
    today_rows = [r for r in all_rows if r.get("dt") and r["dt"].date() == report_date]
    events = db_events(report_date, db_path)

    acct = {"ok": False, "error": "已跳过（--no-api）"}
    if not args.no_api:
        acct = fetch_account(args.env_file)
        if not acct["ok"]:
            print(f"WARN: 账户快照失败，报告中会注明：{acct['error']}", file=sys.stderr)

    history = load_config_history()
    prev_date, prev_cfg = previous_config(history, report_date)

    text = build_report(report_date, cfg, state, log, today_rows, all_rows,
                        events, acct, prev_date, prev_cfg)

    if args.to_stdout:
        print(text)
    else:
        out = REPORTS_DIR / str(report_date.year) / f"{report_date.month:02d}" / f"{report_date.isoformat()}.md"
        if out.exists() and not args.force:
            print(f"ERROR: {out} 已存在。确认要覆盖请加 --force。", file=sys.stderr)
            return 3
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"报告已生成：{out}")

    # Record this run's config so the next report can diff against it.
    if not args.to_stdout:
        history[report_date.isoformat()] = cfg
        save_config_history(history)
        print(f"配置快照已记录：{CONFIG_HISTORY.name}（{len(history)} 个日期）")

    # Console summary
    st = trade_stats(today_rows)
    print(f"今日平仓 {st['n']} 笔，净盈亏 {st['total']:+.4f} USDT", end="")
    if acct.get("ok") and acct.get("account"):
        print(f"；账户余额 {float(acct['account'].get('totalWalletBalance', 0)):.4f} USDT")
    else:
        print("；账户快照不可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
