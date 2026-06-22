#!/bin/bash
# Daemon-launch trader: detach from hermes process tree, redirect stdio to log file.
# This avoids the "stdout pipe to dead hermes session blocks process" bug.
set -u
cd ~/projects/binance_grid_trader
LOG=data/live_trader.log

# Kill any existing trader first
pkill -KILL -f "scripts/live_trader.py" 2>/dev/null
sleep 1

# True daemon: setsid (new session, no controlling tty) + closed stdin + stdout/stderr to file
source .venv/bin/activate
setsid bash -c "exec python -u scripts/live_trader.py >> $LOG 2>&1 < /dev/null" &
disown
sleep 3

# Verify
pid=$(pgrep -f "scripts/live_trader.py" | head -1)
if [[ -n "$pid" ]]; then
    echo "Trader started: PID $pid"
    # Confirm stdout is our file, not a pipe
    out=$(readlink /proc/$pid/fd/1 2>/dev/null)
    err=$(readlink /proc/$pid/fd/2 2>/dev/null)
    echo "  fd/1 (stdout) -> $out"
    echo "  fd/2 (stderr) -> $err"
    if [[ "$out" == *"live_trader.log" ]]; then
        echo "  ✓ stdout properly redirected to log file"
    else
        echo "  ✗ WARNING: stdout still pointing to wrong place"
    fi
else
    echo "FAILED to start trader"
    exit 1
fi
