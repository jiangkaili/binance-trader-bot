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

# Dry-run variants — kept separate so test runs don't pollute the live log, / Dry-run变体 — 保持独立，以免测试运行污染生产机器人使用的实时日志、
# state, or pnl_state files used by the production bot. / 状态或pnl_state文件。
DRYRUN_LOG_PATH = DATA_DIR / "live_trader.dryrun.log"
DRYRUN_STATE_PATH = DATA_DIR / "live_trader.dryrun.state"
DRYRUN_PNL_STATE_PATH = DATA_DIR / "pnl_state.dryrun.json"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
