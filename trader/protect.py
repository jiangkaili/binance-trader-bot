"""Floating-profit protection: decide whether (and where) to move the stop.

Pure functions only — no exchange calls, no clock, no config objects. That way
the rule can be unit-tested exhaustively before it is allowed near real money.

Two stages, both driven by config:

  1. Breakeven — once floating profit reaches `trigger_pct`, move the stop to
     entry ± `lock_pct` so a winner can no longer turn into a loser.
  2. Trailing (optional) — with `trail_pct > 0`, keep pulling the stop along
     behind the mark price, but never backwards.

The decision honours the exchange tick size indirectly: callers round to the
instrument's tick before placing the order, and this module refuses to return a
stop that sits on the wrong side of the current price.

浮盈保护决策（纯函数，便于单测）：
  1. 保本：浮盈达到 trigger_pct 后，把止损推到 entry ± lock_pct，避免盈利单倒亏。
  2. 追踪（可选）：trail_pct > 0 时，止损跟着标记价走，但绝不后退。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ProtectDecision", "floating_pct", "decide_protection"]


@dataclass(frozen=True)
class ProtectDecision:
    """Where the stop should move to, and why."""
    target: float
    reason: str  # "breakeven" | "trail"

    def to_dict(self) -> dict:
        return {"target": self.target, "reason": self.reason}


def floating_pct(is_long: bool, entry: float, mark: float) -> float:
    """Position-side floating PnL as a fraction of entry price.

    Positive = in profit. Returns 0.0 on unusable inputs rather than raising,
    so a bad tick can never trigger a protective action by accident.
    """
    if entry <= 0 or mark <= 0:
        return 0.0
    if is_long:
        return (mark - entry) / entry
    return (entry - mark) / entry


def decide_protection(
    *,
    is_long: bool,
    entry: float,
    mark: float,
    current_stop: float | None,
    trigger_pct: float,
    lock_pct: float,
    trail_pct: float = 0.0,
) -> ProtectDecision | None:
    """Return the stop to move to, or None when nothing should change.

    Contract:
      - None means "leave the resting order alone". Idempotent by construction,
        so it is safe to call every tick.
      - A returned target is always on the profitable side of `mark` (long:
        target < mark, short: target > mark) and strictly better than
        `current_stop` when that is known.
    """
    pct = floating_pct(is_long, entry, mark)
    if pct < trigger_pct:
        return None

    if is_long:
        target = entry * (1.0 + lock_pct)
        reason = "breakeven"
        if trail_pct > 0:
            trail = mark * (1.0 - trail_pct)
            if trail > target:
                target, reason = trail, "trail"
    else:
        target = entry * (1.0 - lock_pct)
        reason = "breakeven"
        if trail_pct > 0:
            trail = mark * (1.0 + trail_pct)
            if trail < target:
                target, reason = trail, "trail"

    # Never move a stop backwards / 止损绝不后退
    if current_stop is not None and current_stop > 0:
        if is_long and target <= current_stop:
            return None
        if not is_long and target >= current_stop:
            return None

    # Never place a stop on the wrong side of the current price / 不能挂到现价错误一侧
    if is_long and target >= mark:
        return None
    if not is_long and target <= mark:
        return None

    return ProtectDecision(target=target, reason=reason)
