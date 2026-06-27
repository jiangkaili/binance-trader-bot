"""Unit tests for the strategy library.

Each test verifies:
  1. The strategy returns valid Signals on synthetic data
  2. The strategy respects warmup (no signals until min_bars)
  3. The strategy emits the right direction for obvious patterns
"""
import numpy as np
import pandas as pd
import pytest

from gridtrader.quant.strategies import (
    STRATEGIES, get_strategy,
    MaCrossStrategy, BollingerStrategy, RsiRevertStrategy, MomentumStrategy,
    Side,
)


def make_synth(n=300, *, freq="h", start="2024-01-01", seed=42):
    np.random.seed(seed)
    return pd.DataFrame(
        {
            "open":  100 + np.cumsum(np.random.randn(n)),
            "high":  100 + np.cumsum(np.random.randn(n)) + 1,
            "low":   100 + np.cumsum(np.random.randn(n)) - 1,
            "close": 100 + np.cumsum(np.random.randn(n)),
            "volume": np.random.randint(100, 1000, n),
        },
        index=pd.date_range(start, periods=n, freq=freq),
    )


# -------- registry -------- / -------- 注册表 --------

def test_all_strategies_registered():
    assert set(STRATEGIES) == {"ma_cross", "bollinger", "rsi_revert", "momentum"}


def test_get_strategy_unknown_raises():
    with pytest.raises(KeyError):
        get_strategy("nope")


def test_get_strategy_with_params():
    st = get_strategy("rsi_revert", period=10, oversold=20.0, overbought=80.0)
    assert st.params["period"] == 10
    assert st.params["oversold"] == 20.0


# -------- warmup -------- / -------- 预热 --------

@pytest.mark.parametrize("name", list(STRATEGIES))
def test_warmup_returns_flat(name):
    """All strategies return FLAT when history is shorter than min_bars."""
    st = get_strategy(name)
    df = make_synth(n=5)  # way too short / 太短了
    sig = st.next_signal(df.iloc[-1], df)
    assert sig.side == Side.FLAT
    assert "warmup" in sig.reason or "history" in sig.reason or "insufficient" in sig.reason


# -------- direction tests -------- / -------- 方向测试 --------

def test_ma_cross_catches_golden_cross():
    """Build a V-shape: down then up, expect at least one BUY."""
    n = 300
    t = np.arange(n)
    prices = np.maximum(100 - 0.5 * t + 0.005 * t * t, 10)
    df = pd.DataFrame(
        {
            "open": prices, "high": prices * 1.001, "low": prices * 0.999,
            "close": prices, "volume": [100] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )
    st = MaCrossStrategy()
    seen_buy = False
    for i in range(st.min_bars, len(df)):
        sig = st.next_signal(df.iloc[i], df.iloc[: i + 1])
        if sig.side == Side.BUY:
            seen_buy = True
            break
    assert seen_buy, "ma_cross should BUY at the V bottom"


def test_bollinger_buys_on_oversold():
    """Force close below lower band and expect a BUY."""
    n = 100
    close = np.full(n, 100.0)
    close[-1] = 50.0  # crash on the last bar / 最后一根K线暴跌
    df = pd.DataFrame(
        {
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": [100] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )
    st = BollingerStrategy()
    sig = st.next_signal(df.iloc[-1], df)
    assert sig.side == Side.BUY
    assert "lower" in sig.reason


def test_rsi_buys_oversold():
    """Pure downtrend → RSI near 0 → expect BUY."""
    n = 100
    df = pd.DataFrame(
        {
            "open":  np.arange(100, 0, -1, dtype=float),
            "high":  np.arange(101, 1, -1, dtype=float),
            "low":   np.arange(99, -1, -1, dtype=float),
            "close": np.arange(100, 0, -1, dtype=float),
            "volume": [100] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )
    st = RsiRevertStrategy()
    sig = st.next_signal(df.iloc[-1], df)
    assert sig.side == Side.BUY
    assert "RSI" in sig.reason


def test_rsi_sells_overbought():
    """Pure uptrend → RSI near 100 → expect SELL."""
    n = 100
    df = pd.DataFrame(
        {
            "open":  np.arange(0, 100, dtype=float),
            "high":  np.arange(1, 101, dtype=float),
            "low":   np.arange(-1, 99, dtype=float),
            "close": np.arange(0, 100, dtype=float),
            "volume": [100] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )
    st = RsiRevertStrategy()
    sig = st.next_signal(df.iloc[-1], df)
    assert sig.side == Side.SELL


def test_momentum_buys_uptrend():
    """Sustained uptrend → positive momentum → BUY."""
    n = 50
    prices = np.linspace(100, 150, n)
    df = pd.DataFrame(
        {
            "open": prices, "high": prices * 1.001, "low": prices * 0.999,
            "close": prices, "volume": [100] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )
    st = MomentumStrategy(period=20, threshold=0.05)
    sig = st.next_signal(df.iloc[-1], df)
    assert sig.side == Side.BUY


# -------- signal shape -------- / -------- 信号形态 --------

@pytest.mark.parametrize("name", list(STRATEGIES))
def test_signal_strength_in_range(name):
    df = make_synth()
    st = get_strategy(name)
    for i in range(st.min_bars, len(df), 10):
        sig = st.next_signal(df.iloc[i], df.iloc[: i + 1])
        assert 0.0 <= sig.strength <= 1.0
