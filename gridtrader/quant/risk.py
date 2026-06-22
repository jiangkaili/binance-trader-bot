"""Risk management.

The RiskManager enforces three classes of limit BEFORE any order is sent
to the gateway:
  1. Position size cap   - max % of account in a single symbol
  2. Single order cap    - max % of account in one order
  3. Daily loss cap      - halt trading after a configurable daily loss
  4. Max open orders     - count of working (not-yet-filled) orders

It also computes position-level state (long/short/net) by replaying
the trade log, so it works identically for paper and live trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from .config import RiskSettings
from .storage import Store


@dataclass
class Position:
    symbol: str
    qty: float = 0.0          # positive = long, negative = short
    avg_price: float = 0.0    # volume-weighted average entry
    realized_pnl: float = 0.0

    @property
    def is_flat(self) -> bool:
        return abs(self.qty) < 1e-12

    @property
    def market_value(self) -> float:
        return self.qty * self.avg_price


@dataclass
class Account:
    """Track equity / daily pnl over time."""
    starting_equity: float
    cash: float = 0.0
    equity: float = 0.0          # cash + unrealized PnL (mark-to-market)

    @classmethod
    def from_equity(cls, equity: float) -> "Account":
        return cls(starting_equity=equity, cash=equity, equity=equity)


class RiskViolation(Exception):
    """Raised when a trade would breach a configured risk limit."""


class RiskManager:
    """Pre-trade checks + state tracking.

    Designed to be called from strategy code:
        risk = RiskManager(settings, store, account)
        ...
        risk.check_order(symbol="BTCUSDT", side="BUY", qty=0.01, price=60000)
    """

    def __init__(self, settings: RiskSettings, store: Store, account: Account):
        self.settings = settings
        self.store = store
        self.account = account
        self._positions: dict[str, Position] = {}
        self._open_order_count: int = 0
        self._daily_pnl: dict[str, float] = {}  # date(iso) -> pnl
        self._daily_reset_date: date = datetime.now(timezone.utc).date()
        self._replay()

    # -------- replay from trade log --------

    def _replay(self) -> None:
        """Rebuild positions + daily PnL by replaying stored trades."""
        for sym, grp in self.store.trades().groupby("symbol"):
            pos = Position(symbol=sym)
            for _, t in grp.iterrows():
                qty = float(t["qty"])
                price = float(t["price"])
                side = t["side"].upper()
                pnl = float(t.get("pnl") or 0.0)
                signed = qty if side == "BUY" else -qty
                self._apply_fill_to_position(pos, signed, price)
                pos.realized_pnl += pnl
            self._positions[sym] = pos
        # Daily pnl
        daily = self.store.daily_pnl()
        for _, r in daily.iterrows():
            self._daily_pnl[r["day"]] = float(r["pnl"])

    @staticmethod
    def _apply_fill_to_position(pos: Position, signed_qty: float, price: float) -> None:
        if pos.is_flat:
            pos.qty = signed_qty
            pos.avg_price = price
            return
        # Same direction?  Add to avg.
        if (signed_qty > 0 and pos.qty > 0) or (signed_qty < 0 and pos.qty < 0):
            new_qty = pos.qty + signed_qty
            pos.avg_price = (pos.avg_price * abs(pos.qty) + price * abs(signed_qty)) / abs(new_qty)
            pos.qty = new_qty
            return
        # Opposite direction?  Reduce or flip.
        if abs(signed_qty) < abs(pos.qty):
            pos.qty += signed_qty
            return
        # Flipped or fully closed — caller computes realized PnL separately
        pos.qty += signed_qty
        if (signed_qty > 0 and pos.qty < 0) or (signed_qty < 0 and pos.qty > 0):
            pos.avg_price = price  # reset avg on flip

    # -------- state mutation (called by strategy / engine) --------

    def register_open_order(self) -> None:
        self._open_order_count += 1

    def register_filled_order(self) -> None:
        if self._open_order_count > 0:
            self._open_order_count -= 1

    def apply_fill(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        fee: float = 0.0,
    ) -> float:
        """Update position state with a new fill. Returns realized PnL of this fill."""
        pos = self._positions.setdefault(symbol, Position(symbol=symbol))
        signed = qty if side.upper() == "BUY" else -qty
        prev_qty = pos.qty
        prev_avg = pos.avg_price
        self._apply_fill_to_position(pos, signed, price)

        # Realized PnL on a reducing fill
        realized = 0.0
        if prev_qty != 0 and ((signed > 0 and prev_qty < 0) or (signed < 0 and prev_qty > 0)):
            reducing = min(abs(signed), abs(prev_qty))
            if prev_qty > 0:
                realized = (price - prev_avg) * reducing
            else:
                realized = (prev_avg - price) * reducing
            realized -= fee
            pos.realized_pnl += realized
            # account cash flow
            self.account.cash += realized + (-signed * price)  # notional flip-back
        else:
            # Opening / adding — debit cash for notional + fee
            self.account.cash -= (signed * price) + fee

        # Daily pnl
        today = datetime.now(timezone.utc).date().isoformat()
        self._daily_pnl[today] = self._daily_pnl.get(today, 0.0) + realized
        return realized

    def mark_to_market(self, prices: dict[str, float]) -> None:
        """Update account equity with current prices."""
        unrealized = 0.0
        for sym, pos in self._positions.items():
            if pos.is_flat:
                continue
            px = prices.get(sym, pos.avg_price)
            if pos.qty > 0:
                unrealized += (px - pos.avg_price) * pos.qty
            else:
                unrealized += (pos.avg_price - px) * abs(pos.qty)
        self.account.equity = self.account.cash + unrealized

    # -------- pre-trade checks --------

    def check_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> None:
        """Raise RiskViolation if the order would breach any limit."""
        if qty <= 0 or price <= 0:
            raise RiskViolation(f"Invalid order: qty={qty} price={price}")
        order_value = qty * price
        equity = max(self.account.equity, 1.0)  # avoid div by zero

        # 1) Single order cap
        if order_value / equity > self.settings.max_single_order_value_pct:
            raise RiskViolation(
                f"Order value {order_value:.2f} exceeds "
                f"{self.settings.max_single_order_value_pct*100:.1f}% of equity"
            )

        # 2) Max open orders
        if self._open_order_count >= self.settings.max_open_orders:
            raise RiskViolation(
                f"Already at max open orders ({self.settings.max_open_orders})"
            )

        # 3) Position cap (post-trade)
        pos = self._positions.get(symbol, Position(symbol=symbol))
        new_qty = pos.qty + (qty if side.upper() == "BUY" else -qty)
        new_value = abs(new_qty) * price
        if new_value / equity > self.settings.max_position_pct:
            raise RiskViolation(
                f"Post-trade position {new_value:.2f} would exceed "
                f"{self.settings.max_position_pct*100:.1f}% of equity"
            )

        # 4) Daily loss cap
        today = datetime.now(timezone.utc).date().isoformat()
        day_pnl = self._daily_pnl.get(today, 0.0)
        max_loss = -self.settings.max_daily_loss_pct * self.account.starting_equity
        if day_pnl <= max_loss:
            raise RiskViolation(
                f"Daily loss cap hit: {day_pnl:.2f} <= {max_loss:.2f} "
                f"(limit {self.settings.max_daily_loss_pct*100:.1f}%)"
            )

    # -------- introspection --------

    def position(self, symbol: str) -> Position:
        return self._positions.get(symbol, Position(symbol=symbol))

    def daily_pnl(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        return self._daily_pnl.get(today, 0.0)

    def snapshot(self) -> dict:
        return {
            "account": {
                "starting_equity": self.account.starting_equity,
                "cash": self.account.cash,
                "equity": self.account.equity,
            },
            "open_orders": self._open_order_count,
            "daily_pnl": self.daily_pnl(),
            "positions": {
                sym: {
                    "qty": p.qty,
                    "avg_price": p.avg_price,
                    "realized_pnl": p.realized_pnl,
                }
                for sym, p in self._positions.items()
            },
        }
