#!/usr/bin/env bash
# Start the live trader in the background with nohup.
# Usage:  bash scripts/start_trader.sh
#         bash scripts/start_trader.sh --dry-run   # paper mode
set -e
cd "$(dirname "$0")/.."

DRY=""
if [[ "$1" == "--dry-run" ]]; then
    DRY="--dry-run"
    echo "Starting in DRY-RUN mode (no real orders)"
else
    echo "Starting in LIVE mode (REAL MONEY)"
fi

PID_FILE="data/live_trader.pid"
LOG_FILE="data/live_trader.log"
mkdir -p data

# Check if already running
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Already running with PID $OLD_PID. To restart: bash scripts/stop_trader.sh first."
        exit 1
    else
        echo "Stale PID file detected, removing"
        rm -f "$PID_FILE"
    fi
fi

# Activate venv if it exists
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
fi

# Start in background, detached from terminal
nohup python scripts/live_trader.py $DRY >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# Give it a moment to start
sleep 2

if kill -0 "$PID" 2>/dev/null; then
    echo "Started with PID $PID"
    echo "  Log:   $LOG_FILE"
    echo "  State: data/live_trader.state"
    echo "  DB:    data/trades.db"
    echo "  To stop: bash scripts/stop_trader.sh"
    echo "  To watch: tail -f $LOG_FILE"
else
    echo "FAILED to start. Last 20 lines of log:"
    tail -20 "$LOG_FILE"
    exit 1
fi
