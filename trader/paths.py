"""Single source of truth for filesystem paths used by the trader."""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "trader.yaml"
TRADES_DB_PATH = DATA_DIR / "trades.db"
LOG_PATH = DATA_DIR / "live_trader.log"
STATE_PATH = DATA_DIR / "live_trader.state"
PNL_STATE_PATH = DATA_DIR / "pnl_state.json"
KILLSWITCH_PATH = DATA_DIR / "KILLSWITCH"
COOLDOWN_PATH = DATA_DIR / "COOLDOWN_UNTIL"
