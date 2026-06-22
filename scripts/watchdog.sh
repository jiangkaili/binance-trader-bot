#!/bin/bash
# Trader watchdog: ensures live_trader.py is always running.
# Restarts it if dead. Logs to data/watchdog.log. Silent if all is well.
set -u
cd ~/projects/binance_grid_trader
LOG=data/watchdog.log

if pgrep -f "scripts/live_trader.py" > /dev/null; then
    # 进程在跑，检查 state 是否在更新（最近 5 分钟有 tick）
    if [[ -f data/live_trader.state ]]; then
        ts=$(stat -c %Y data/live_trader.state 2>/dev/null || echo 0)
        now=$(date +%s)
        age=$((now - ts))
        if [[ $age -gt 600 ]]; then
            echo "[$(date -Iseconds)] STALE: state file not updated for ${age}s, restarting" >> $LOG
            pkill -TERM -f "scripts/live_trader.py" 2>/dev/null
            sleep 5
            pkill -KILL -f "scripts/live_trader.py" 2>/dev/null
        else
            exit 0  # 健康，静默退出
        fi
    fi
fi

# 进程不在或被杀了，重启
echo "[$(date -Iseconds)] RESTART: trader not running, starting fresh" >> $LOG
source .venv/bin/activate
nohup python scripts/live_trader.py > /dev/null 2>&1 &
disown
sleep 3
new_pid=$(pgrep -f "scripts/live_trader.py" | head -1)
if [[ -n "$new_pid" ]]; then
    echo "[$(date -Iseconds)] STARTED: PID $new_pid" >> $LOG
else
    echo "[$(date -Iseconds)] FAILED to start trader" >> $LOG
fi
