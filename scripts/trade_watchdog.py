#!/usr/bin/env python3
"""Watchdog: only output when a new trade happens or bot stops.
Silent otherwise (empty stdout = no notification)."""
import json, sqlite3, os, subprocess
from datetime import timezone, timedelta

DATA = "/mnt/c/Users/admin/binance_trader/data"
STATE = f"{DATA}/live_trader.state"
TRADES_DB = f"{DATA}/trades.db"
LOG = f"{DATA}/live_trader.log"
LAST_SEEN = "/tmp/.bot_last_trade_id"

# Check if bot process is alive / 检查机器人进程是否存活
try:
    result = subprocess.run(
        ["/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile",
         "-Command", "Get-Process pythonw -ErrorAction SilentlyContinue | Measure-Object | Select-Object -ExpandProperty Count"],
        capture_output=True, text=True, timeout=10
    )
    proc_count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
except Exception:
    proc_count = -1

if proc_count == 0:
    print("⚠️ Bot process NOT running! Watchdog should restart it, but please check.")
    # Don't exit here, still check trades / 不在此退出，仍检查交易

# Read last seen trade ID / 读取上次看到的交易ID
last_id = 0
if os.path.exists(LAST_SEEN):
    with open(LAST_SEEN) as f:
        last_id = int(f.read().strip() or "0")

# Check for new trades / 检查新交易
new_trades = []
if os.path.exists(TRADES_DB):
    conn = sqlite3.connect(TRADES_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM trades WHERE id > ? ORDER BY id ASC", (last_id,))
        rows = cur.fetchall()
        for r in rows:
            new_trades.append({k: r[k] for k in r.keys()})
        # Update last seen / 更新上次看到的ID
        if rows:
            last_id = rows[-1]["id"]
            with open(LAST_SEEN, "w") as f:
                f.write(str(last_id))
    except Exception:
        pass
    conn.close()

# Output new trades if any / 如果有新交易则输出
if new_trades:
    bj = timezone(timedelta(hours=8))
    print(f"🔔 {len(new_trades)} new trade(s) detected!\n")
    for t in new_trades:
        ts = t.get("opened_at", t.get("ts", "?"))
        side = t.get("side", "?")
        entry = t.get("entry_price", "?")
        exit_p = t.get("exit_price", "?")
        pnl = t.get("realized_pnl", "?")
        reason = t.get("close_reason", t.get("close_reason_code", ""))
        print(f"  Trade #{t.get('id','?')}: {side} entry={entry} exit={exit_p}")
        print(f"    PnL={pnl} | Reason={reason}")
        print(f"    Opened: {ts}")

    # Read current state for context / 读取当前状态作为上下文
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)
        print(f"\n  Current: signal={state.get('signal','?')} pos={state.get('position','?')} "
              f"daily_pnl={state.get('daily_pnl','?')} weekly_pnl={state.get('weekly_pnl','?')}")
    print("\nSee full details: https://github.com/jiangkaili/binance-trader-bot")
# else: silent — no output = no notification / 否则：静默 — 无输出 = 无通知
