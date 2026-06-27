"""Strategy library.

Each strategy is a class that operates on a pandas DataFrame of OHLCV bars
and emits BUY / SELL / FLAT signals. They are deliberately pure (no
exchange calls) so the same code can run in backtest, paper, and live.

Indicators are recomputed from `history` on every call, so the strategy
holds no per-bar state and `next_signal` is a pure function of
(history, bar). This makes backtest results reproducible and live
deployment a non-event.

To add a new strategy:
  1. Subclass `Strategy` and implement `next_signal(bar, history) -> Signal`
  2. Register it in STRATEGIES (name -> class) so the CLI can find it
  3. Add a unit test under tests/test_strategies.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import pandas as pd

from . import indicators as ind


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"  # no action / close / 无操作 / 平仓


@dataclass
class Signal:
    side: Side
    strength: float = 1.0  # 0..1, sizing hint / 0..1，仓位大小提示
    reason: str = ""


class Strategy(ABC):
    """Base class — strategies consume bars and emit signals.

    Lifecycle:
      1. ctor: receive params
      2. on_bars(df): receive historical warmup data (no side effects required)
      3. next_signal(bar, history): called for each new bar after warmup
         - `bar`     = the latest bar (df.iloc[-1])
         - `history` = df.iloc[: i + 1] for the current i
         Implementations should derive their indicators from `history` so
         the function is pure with respect to (history, bar).
    """

    name: str = "base"
    description: str = ""
    default_params: dict = {}
    min_bars: int = 1  # minimum warmup needed before signals are valid / 信号有效前所需的最小预热期

    def __init__(self, **params):
        self.params = {**self.default_params, **params}

    def on_bars(self, df: pd.DataFrame) -> None:
        """Optional warmup hook. Default: no-op (strategies compute on demand)."""
        pass

    @abstractmethod
    def next_signal(self, bar: pd.Series, history: pd.DataFrame) -> Signal:
        """Emit a signal for the latest bar. Return FLAT to do nothing."""
        raise NotImplementedError


# -------- concrete strategies -------- / -------- 具体策略 --------

class MaCrossStrategy(Strategy):
    """Classic fast/slow moving-average crossover.

    BUY  when fast EMA crosses above slow EMA (golden cross)
    SELL when fast EMA crosses below slow EMA (death cross)
    """

    name = "ma_cross"
    description = "EMA fast/slow crossover"
    default_params = {"fast": 12, "slow": 26}
    min_bars = 30

    def next_signal(self, bar: pd.Series, history: pd.DataFrame) -> Signal:
        if len(history) < self.params["slow"] + 2:
            return Signal(Side.FLAT, reason="warmup")
        close = history["close"]
        fast = ind.ema(close, self.params["fast"])
        slow = ind.ema(close, self.params["slow"])
        f_now, s_now = float(fast.iloc[-1]), float(slow.iloc[-1])
        f_prev, s_prev = float(fast.iloc[-2]), float(slow.iloc[-2])
        if f_prev <= s_prev and f_now > s_now:
            return Signal(Side.BUY, reason=f"golden cross fast={f_now:.4f} slow={s_now:.4f}")
        if f_prev >= s_prev and f_now < s_now:
            return Signal(Side.SELL, reason=f"death cross fast={f_now:.4f} slow={s_now:.4f}")
        return Signal(Side.FLAT, reason="no cross")


class BollingerStrategy(Strategy):
    """Mean reversion on Bollinger Bands.

    BUY  when close < lower band  (oversold)
    SELL when close > upper band  (overbought)
    """

    name = "bollinger"
    description = "Bollinger Band mean reversion"
    default_params = {"period": 20, "num_std": 2.0}
    min_bars = 25

    def next_signal(self, bar: pd.Series, history: pd.DataFrame) -> Signal:
        if len(history) < self.params["period"] + 1:
            return Signal(Side.FLAT, reason="warmup")
        bb = ind.bollinger(history["close"], self.params["period"], self.params["num_std"])
        close = float(bar["close"])
        upper = float(bb["upper"].iloc[-1])
        lower = float(bb["lower"].iloc[-1])
        if close < lower:
            strength = min(1.0, (lower - close) / max(lower * 0.01, 1e-9))
            return Signal(Side.BUY, strength=strength, reason=f"close {close:.2f} < lower {lower:.2f}")
        if close > upper:
            strength = min(1.0, (close - upper) / max(upper * 0.01, 1e-9))
            return Signal(Side.SELL, strength=strength, reason=f"close {close:.2f} > upper {upper:.2f}")
        return Signal(Side.FLAT, reason="within bands")


class RsiRevertStrategy(Strategy):
    """RSI mean reversion.

    BUY  when RSI < oversold  (default 30)
    SELL when RSI > overbought (default 70)
    """

    name = "rsi_revert"
    description = "RSI oversold/overbought"
    default_params = {"period": 14, "oversold": 30.0, "overbought": 70.0}
    min_bars = 20

    def next_signal(self, bar: pd.Series, history: pd.DataFrame) -> Signal:
        if len(history) < self.params["period"] + 1:
            return Signal(Side.FLAT, reason="warmup")
        r = ind.rsi(history["close"], self.params["period"])
        v = float(r.iloc[-1])
        if v < self.params["oversold"]:
            strength = min(1.0, (self.params["oversold"] - v) / 30.0)
            return Signal(Side.BUY, strength=strength, reason=f"RSI={v:.1f} < {self.params['oversold']}")
        if v > self.params["overbought"]:
            strength = min(1.0, (v - self.params["overbought"]) / 30.0)
            return Signal(Side.SELL, strength=strength, reason=f"RSI={v:.1f} > {self.params['overbought']}")
        return Signal(Side.FLAT, reason=f"RSI={v:.1f} neutral")


class MomentumStrategy(Strategy):
    """Time-series momentum: buy when N-period return > threshold.

    Symmetric version: BUY on positive momentum, SELL on negative.
    Long-only mode is available via `long_only=True`.
    """

    name = "momentum"
    description = "N-period momentum threshold"
    default_params = {"period": 20, "threshold": 0.02, "long_only": True}
    min_bars = 25

    def next_signal(self, bar: pd.Series, history: pd.DataFrame) -> Signal:
        if len(history) < self.params["period"] + 1:
            return Signal(Side.FLAT, reason="warmup")
        m = ind.momentum(history["close"], self.params["period"])
        v = float(m.iloc[-1])
        if pd.isna(v):
            return Signal(Side.FLAT, reason="warmup")
        th = self.params["threshold"]
        if v > th:
            return Signal(Side.BUY, strength=min(1.0, v / (th * 3)), reason=f"mom={v:.4f} > {th}")
        if v < -th and not self.params.get("long_only", True):
            return Signal(Side.SELL, strength=min(1.0, -v / (th * 3)), reason=f"mom={v:.4f} < -{th}")
        return Signal(Side.FLAT, reason=f"mom={v:.4f} within ±{th}")


# -------- registry -------- / -------- 注册表 --------

STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (MaCrossStrategy, BollingerStrategy, RsiRevertStrategy, MomentumStrategy)
}


def get_strategy(name: str, **params) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(
            f"Unknown strategy '{name}'. Available: {sorted(STRATEGIES)}"
        )
    return STRATEGIES[name](**params)
