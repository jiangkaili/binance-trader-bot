"""Risk management — killswitch, cooldown, daily/weekly loss caps.

Pulled out of LiveTrader.can_open_new(), reset_daily(), _check_streak()
plus stop-loss/take-profit checks for symmetry.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .config import TraderConfig
from .models import Position
from .paths import COOLDOWN_PATH, KILLSWITCH_PATH, TRADES_DB_PATH


@dataclass
class RiskState:
    """Per-process risk counters — persisted via state module, not here."""
    starting_equity: float = 0.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    last_date: date = field(default_factory=date.today)


class RiskManager:
    def __init__(self, cfg: TraderConfig, state: RiskState, log: Callable[[str, str], None]):
        self.cfg = cfg
        self.state = state
        self.log = log

    # ----- daily / weekly boundary -----

    def reset_daily(self) -> None:
        today = date.today()
        if today != self.state.last_date:
            self.state.last_date = today
            self.state.daily_pnl = 0.0
            if today.weekday() == 0:  # Monday
                self.state.weekly_pnl = 0.0
            self.log("INFO", "new day/week — daily/weekly counters reset")

    # ----- gate -----

    def can_open_new(self) -> tuple[bool, str]:
        s = self.state
        c = self.cfg

        if KILLSWITCH_PATH.exists():
            try:
                reason = KILLSWITCH_PATH.read_text().strip()[:200]
            except OSError as e:
                self.log("WARN", f"killswitch file unreadable: {e}")
                reason = "(unknown)"
            return False, f"KILLSWITCH active: {reason}"

        if COOLDOWN_PATH.exists():
            try:
                until = float(COOLDOWN_PATH.read_text().strip())
                if time.time() < until:
                    return False, f"24h cooldown after losing streak: {int(until - time.time())}s remaining"
                COOLDOWN_PATH.unlink()
            except (OSError, ValueError) as e:
                self.log("WARN", f"cooldown file parse failed: {e}")

        daily_loss = -s.daily_pnl
        if daily_loss >= c.daily_loss_pct * s.starting_equity:
            return False, f"daily loss cap hit: -${daily_loss:.4f} >= ${c.daily_loss_pct * s.starting_equity:.4f}"

        weekly_loss = -s.weekly_pnl
        if weekly_loss >= c.weekly_loss_pct * s.starting_equity:
            return False, f"weekly loss cap hit: -${weekly_loss:.4f} >= ${c.weekly_loss_pct * s.starting_equity:.4f}"

        # Permanent -10% cumulative kill-switch (excludes backfilled rows).
        try:
            conn = sqlite3.connect(str(TRADES_DB_PATH))
            cum_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades "
                "WHERE order_id IS NULL OR order_id NOT LIKE 'backfilled_%'"
            ).fetchone()[0]
            conn.close()
            if cum_pnl <= -0.10 * s.starting_equity:
                KILLSWITCH_PATH.write_text(
                    f"auto-killswitch: cumulative pnl {cum_pnl:.4f} <= -10% of {s.starting_equity}"
                )
                self.log("CRITICAL", f"AUTO KILL-SWITCH: cumulative loss {cum_pnl:.4f} hit -10% of starting equity (excl. backfilled)")
                return False, f"auto kill-switch triggered: cum_pnl={cum_pnl:.4f}"
        except sqlite3.Error as e:
            self.log("WARN", f"could not check cumulative pnl: {e}")

        if s.starting_equity <= 0:
            return False, "no equity"
        return True, "ok"

    # ----- position-level stops (mirror exchange algo orders) -----

    def hit_stop_loss(self, pos: Position) -> bool:
        if pos.pct_change < -self.cfg.stop_loss_pct:
            self.log(
                "WARNING",
                f"STOP-LOSS hit: {self.cfg.symbol} {pos.side} "
                f"change={pos.pct_change*100:.3f}% < -{self.cfg.stop_loss_pct*100:.2f}%; uPnl={pos.u_pnl:.4f}",
            )
            return True
        return False

    def hit_take_profit(self, pos: Position) -> bool:
        if pos.pct_change >= self.cfg.take_profit_pct:
            self.log(
                "INFO",
                f"TAKE-PROFIT hit: {self.cfg.symbol} {pos.side} "
                f"change={pos.pct_change*100:.3f}% >= +{self.cfg.take_profit_pct*100:.2f}%; uPnl={pos.u_pnl:.4f}",
            )
            return True
        return False

    # ----- streak detection -----

    def check_streak(self) -> None:
        """If last 3 LIVE trades all lost, set 24h cooldown."""
        try:
            conn = sqlite3.connect(str(TRADES_DB_PATH))
            recent = conn.execute(
                "SELECT pnl FROM trades WHERE source='live' ORDER BY ts DESC LIMIT 3"
            ).fetchall()
            conn.close()
            if len(recent) == 3 and all(float(r[0]) < 0 for r in recent):
                until = time.time() + 86400
                COOLDOWN_PATH.write_text(str(until))
                self.log(
                    "CRITICAL",
                    f"3 CONSECUTIVE LOSSES — 24h COOLDOWN until {time.strftime('%F %T', time.localtime(until))}",
                )
        except sqlite3.Error as e:
            self.log("WARN", f"streak check failed: {e}")
