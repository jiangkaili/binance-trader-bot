#!/usr/bin/env bash
# Gracefully stop the live trader.
# Sends SIGTERM — script catches, closes position, exits cleanly.
set -e
cd "$(dirname "$0")/.."

PID_FILE="data/live_trader.pid"
if [[ ! -f "$PID_FILE" ]]; then
    echo "No PID file at $PID_FILE — is the trader running?"
    exit 1
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID not running (stale PID file)"
    rm -f "$PID_FILE"
    exit 1
fi

echo "Sending SIGTERM to PID $PID (script will close position and exit)..."
kill -TERM "$PID"

# Wait up to 30 seconds for clean exit
for i in $(seq 1 30); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Stopped cleanly after ${i}s"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "Process did not exit after 30s, sending SIGKILL (no graceful close!)"
kill -KILL "$PID" || true
rm -f "$PID_FILE"
echo "Force killed. CHECK BINANCE UI FOR OPEN POSITIONS — may need manual close."
