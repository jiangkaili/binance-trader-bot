#!/usr/bin/env bash
# ============================================================
# Quick start: dry-run (no real orders, just watch signals)
# ============================================================
#   ./scripts/run_trader.sh --dry-run
#
# Live trading (real money):
#   ./scripts/run_trader.sh
#
# Background:
#   nohup ./scripts/run_trader.sh > data/live_trader.log 2>&1 &
#
# Stop:
#   kill -TERM $(cat data/trader.pid)
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

# Create venv if missing
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

# Create .env from example if missing
if [ ! -f ".env" ]; then
    echo "WARNING: .env not found. Copying from .env.example..."
    echo "Edit .env with your Binance API keys before going live!"
    cp .env.example .env
fi

# Create data directory
mkdir -p data

# Run
exec .venv/bin/python scripts/live_trader.py "$@"
