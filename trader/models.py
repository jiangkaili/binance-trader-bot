"""Typed domain models — replace the dict-based Position used throughout v1."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Position:
    side: str          # "LONG" or "SHORT"
    qty: float
    entry: float
    mark: float
    u_pnl: float
    leverage: str

    @property
    def is_long(self) -> bool:
        return self.side == "LONG"

    @property
    def pct_change(self) -> float:
        """Position-side P&L as a fraction of entry price."""
        if self.entry <= 0:
            return 0.0
        if self.is_long:
            return (self.mark - self.entry) / self.entry
        return (self.entry - self.mark) / self.entry

    def to_dict(self) -> dict:
        """Backwards-compat dict for legacy callers and state dump."""
        return {
            "side": self.side, "qty": self.qty, "entry": self.entry,
            "mark": self.mark, "uPnl": self.u_pnl, "leverage": self.leverage,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(
            side=d["side"], qty=float(d["qty"]),
            entry=float(d["entry"]), mark=float(d["mark"]),
            u_pnl=float(d.get("uPnl", d.get("u_pnl", 0.0))),
            leverage=str(d.get("leverage", "")),
        )
