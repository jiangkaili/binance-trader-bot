"""Backtesting engine.

A simple, transparent backtester:
  - Replays bars one at a time
  - Asks the strategy for a signal at each bar
  - Manages position state, cash, and a trade log
  - Computes performance metrics at the end

Design choices:
  - No leverage (spot-style). To add futures, allow signed qty > 0 as short.
  - No fees by default (set commission_bps to model maker/taker fees).
  - No slippage by default (set slippage_bps for realism).
  - Long-only unless the strategy is configured to allow shorts.

This is intentionally simple so the numbers are auditable. For a more
heavyweight engine (parameter optimization, multi-symbol, etc.) consider
backtesting.py or vectorbt — but for "what would this strategy have done
on this data?" this is enough and easier to verify.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .strategies import Strategy, Side, Signal, get_strategy


@dataclass
class Trade:
    ts: pd.Timestamp
    symbol: str
    side: str
    price: float
    qty: float
    fee: float = 0.0
    pnl: float = 0.0  # realized pnl of this fill (0 on opens)
    reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    params: dict
    start: pd.Timestamp
    end: pd.Timestamp
    initial_cash: float
    final_equity: float
    trades: list[Trade]
    equity_curve: pd.Series  # indexed by bar ts
    metrics: dict


class Backtester:
    """Run a strategy against historical bars."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        initial_cash: float = 10_000.0,
        commission_bps: float = 10.0,  # 0.10% per side (Binance spot default tier)
        slippage_bps: float = 1.0,      # 0.01% per fill
        position_size_pct: float = 0.95,  # use 95% of cash per entry
        allow_short: bool = False,
    ):
        self.strategy = strategy
        self.initial_cash = initial_cash
        self.commission_bps = commission_bps
        self.slippage_bps = slippage_bps
        self.position_size_pct = position_size_pct
        self.allow_short = allow_short
        self.cash = initial_cash
        self.qty = 0.0
        self.avg_price = 0.0
        self.trades: list[Trade] = []
        self.equity_points: list[tuple[pd.Timestamp, float]] = []

    def run(self, df: pd.DataFrame, symbol: str = "BACKTEST") -> BacktestResult:
        """Run the backtest on a DataFrame with columns: open,high,low,close,volume.

        Index is expected to be a DatetimeIndex.
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("df must have a DatetimeIndex")
        if len(df) < self.strategy.min_bars + 1:
            raise ValueError(
                f"need at least {self.strategy.min_bars + 1} bars, got {len(df)}"
            )

        # Warmup
        self.strategy.on_bars(df)

        # Iterate
        last_idx = len(df) - 1
        for i in range(self.strategy.min_bars, len(df)):
            bar = df.iloc[i]
            history = df.iloc[: i + 1]
            sig = self.strategy.next_signal(bar, history)
            if sig.side != Side.FLAT:
                self._process_signal(bar, sig, symbol)
            # mark to market
            equity = self.cash + self.qty * float(bar["close"])
            self.equity_points.append((df.index[i], equity))

        # Close any open position at last bar
        if self.qty != 0:
            last_close = float(df.iloc[-1]["close"])
            self._close_position(df.index[-1], last_close, symbol, reason="end-of-backtest")

        # Build result
        eq = pd.Series(
            [v for _, v in self.equity_points],
            index=[t for t, _ in self.equity_points],
            name="equity",
        )
        m = compute_metrics(eq, self.trades, self.initial_cash)
        return BacktestResult(
            symbol=symbol,
            strategy=self.strategy.name,
            params=self.strategy.params,
            start=df.index[self.strategy.min_bars],
            end=df.index[-1],
            initial_cash=self.initial_cash,
            final_equity=float(eq.iloc[-1]) if len(eq) else self.initial_cash,
            trades=self.trades,
            equity_curve=eq,
            metrics=m,
        )

    # -------- internal --------

    def _process_signal(self, bar: pd.Series, sig: Signal, symbol: str) -> None:
        price = float(bar["close"])
        # Apply slippage
        if sig.side == Side.BUY:
            fill_price = price * (1 + self.slippage_bps / 10_000)
        else:
            fill_price = price * (1 - self.slippage_bps / 10_000)
        commission = (self.commission_bps / 10_000) * fill_price

        if sig.side == Side.BUY and self.qty <= 0:
            self._open_long(bar.name, fill_price, commission, symbol, sig)
        elif sig.side == Side.SELL and self.qty > 0:
            self._close_position(bar.name, fill_price, symbol, reason=sig.reason, commission=commission)
        elif sig.side == Side.SELL and self.qty == 0 and self.allow_short:
            self._open_short(bar.name, fill_price, commission, symbol, sig)
        elif sig.side == Side.BUY and self.qty < 0 and self.allow_short:
            self._close_position(bar.name, fill_price, symbol, reason=sig.reason, commission=commission)

    def _open_long(self, ts, price: float, commission: float, symbol: str, sig: Signal) -> None:
        cash_to_use = max(self.cash * self.position_size_pct, 0.0)
        if cash_to_use <= 0:
            return
        qty = cash_to_use / (price + commission)
        if qty <= 0:
            return
        # Commission charged on notional
        fee = self.commission_bps / 10_000 * price * qty
        cost = qty * price + fee
        if cost > self.cash:
            return
        self.cash -= cost
        # Update avg
        if self.qty >= 0:
            new_qty = self.qty + qty
            if new_qty > 0:
                self.avg_price = (self.avg_price * self.qty + price * qty) / new_qty
            self.qty = new_qty
        t = Trade(ts=ts, symbol=symbol, side="BUY", price=price, qty=qty, fee=fee, reason=sig.reason)
        self.trades.append(t)

    def _open_short(self, ts, price: float, commission: float, symbol: str, sig: Signal) -> None:
        cash_to_use = max(self.cash * self.position_size_pct, 0.0)
        if cash_to_use <= 0:
            return
        qty = cash_to_use / price
        fee = self.commission_bps / 10_000 * price * qty
        self.cash += qty * price - fee  # short proceeds
        self.qty -= qty
        self.avg_price = price
        t = Trade(ts=ts, symbol=symbol, side="SELL", price=price, qty=qty, fee=fee, reason=sig.reason)
        self.trades.append(t)

    def _close_position(self, ts, price: float, symbol: str, *, reason: str = "", commission: float = 0.0) -> None:
        if self.qty == 0:
            return
        if self.qty > 0:
            pnl = (price - self.avg_price) * self.qty
        else:
            pnl = (self.avg_price - price) * abs(self.qty)
        fee = self.commission_bps / 10_000 * price * abs(self.qty)
        pnl -= fee
        if self.qty > 0:
            self.cash += self.qty * price - fee
            t = Trade(ts=ts, symbol=symbol, side="SELL", price=price, qty=self.qty, fee=fee, pnl=pnl, reason=reason)
        else:
            self.cash -= abs(self.qty) * price - fee
            t = Trade(ts=ts, symbol=symbol, side="BUY", price=price, qty=abs(self.qty), fee=fee, pnl=pnl, reason=reason)
        self.trades.append(t)
        self.qty = 0.0
        self.avg_price = 0.0


# -------- metrics --------


def compute_metrics(
    equity: pd.Series,
    trades: list[Trade],
    initial_cash: float,
    periods_per_year: int = 365 * 24,  # default = hourly bars
) -> dict:
    """Compute standard performance metrics. All return values are floats."""
    if equity.empty:
        return {"error": "empty equity curve"}

    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)

    # Annualized return
    n_bars = len(equity)
    if n_bars > 1:
        years = max(n_bars / periods_per_year, 1e-9)
        cagr = (equity.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    # Sharpe (assuming rf=0)
    if len(returns) > 1:
        std_val = float(returns.std())
        if std_val > 0:
            sharpe = float(returns.mean()) / std_val * math.sqrt(periods_per_year)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Max drawdown
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_dd = float(drawdown.min())  # negative number

    # Trade stats
    closed = [t for t in trades if t.pnl != 0]
    n_trades = len(closed)
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl < 0]
    win_rate = len(wins) / n_trades if n_trades else 0.0
    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0
    profit_factor = (
        abs(sum(t.pnl for t in wins)) / abs(sum(t.pnl for t in losses))
        if losses and sum(t.pnl for t in losses) != 0
        else float("inf") if wins else 0.0
    )

    return {
        "total_return": total_return,
        "cagr": float(cagr),
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "n_bars": n_bars,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": float(profit_factor) if profit_factor != float("inf") else profit_factor,
        "final_equity": float(equity.iloc[-1]),
    }


def format_metrics(m: dict) -> str:
    """Format metrics dict as a readable multi-line string."""
    if "error" in m:
        return f"error: {m['error']}"
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (
        f"  total return : {m['total_return']*100:>8.2f}%\n"
        f"  CAGR         : {m['cagr']*100:>8.2f}%\n"
        f"  Sharpe       : {m['sharpe']:>8.2f}\n"
        f"  Max drawdown : {m['max_drawdown']*100:>8.2f}%\n"
        f"  Trades       : {m['n_trades']:>8d}  (win rate {m['win_rate']*100:>5.1f}%)\n"
        f"  Avg win/loss : {m['avg_win']:>8.2f} / {m['avg_loss']:>6.2f}\n"
        f"  Profit factor: {pf_s:>8s}\n"
        f"  Final equity : {m['final_equity']:>8.2f}"
    )
