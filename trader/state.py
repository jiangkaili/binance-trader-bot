"""State persistence — pnl_state.json + live_trader.state JSON dump."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Callable

from .config import TraderConfig
from .models import Position
from .paths import PNL_STATE_PATH, STATE_PATH
from .risk import RiskState


def load_pnl_state(state: RiskState, log: Callable[[str, str], None]) -> None:
    """Restore daily/weekly pnl from disk so risk caps survive restarts."""
    try:
        with open(PNL_STATE_PATH, "r") as f:
            s = json.load(f)
        today = date.today()
        if s.get("date") == today.isoformat():
            state.daily_pnl = float(s.get("daily_pnl", 0.0))
        iso = today.isocalendar()
        current_week = f"{iso[0]}-{iso[1]}"
        if s.get("week") == current_week:
            state.weekly_pnl = float(s.get("weekly_pnl", 0.0))
        if state.daily_pnl or state.weekly_pnl:
            log("INFO", f"restored pnl state: daily={state.daily_pnl:+.4f} weekly={state.weekly_pnl:+.4f}")
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        pass


def save_pnl_state(state: RiskState, log: Callable[[str, str], None]) -> None:
    try:
        today = date.today()
        iso = today.isocalendar()
        with open(PNL_STATE_PATH, "w") as f:
            json.dump({
                "date": today.isoformat(),
                "week": f"{iso[0]}-{iso[1]}",
                "daily_pnl": state.daily_pnl,
                "weekly_pnl": state.weekly_pnl,
            }, f)
    except OSError as e:
        log("WARN", f"pnl_state save failed: {e}")


def dump_state(
    cfg: TraderConfig,
    state: RiskState,
    position: Position | None,
    tick: int,
    signal: str,
    dry_run: bool,
    log: Callable[[str, str], None],
) -> None:
    """Inspector-friendly snapshot to data/live_trader.state."""
    payload = {
        "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "tick": tick,
        "signal": signal,
        "starting_equity": state.starting_equity,
        "daily_pnl": state.daily_pnl,
        "weekly_pnl": state.weekly_pnl,
        "position": position.to_dict() if position else None,
        "dry_run": dry_run,
        "strategy": cfg.strategy_name,
        "constraints": {
            "leverage": cfg.leverage,
            "target_position_usdt": cfg.target_position_usdt,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "daily_loss_pct": cfg.daily_loss_pct,
            "weekly_loss_pct": cfg.weekly_loss_pct,
        },
    }
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
    except OSError as e:
        log("WARN", f"could not dump state: {e}")
    save_pnl_state(state, log)
