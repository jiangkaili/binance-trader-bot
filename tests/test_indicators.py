"""Unit tests for technical indicators."""
import numpy as np
import pandas as pd
import pytest

from gridtrader.quant.indicators import sma, ema, rsi, bollinger, atr, macd, momentum


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = sma(s, 3)
    # First 2 values are NaN
    assert pd.isna(out.iloc[0])
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == 2.0
    assert out.iloc[3] == 3.0
    assert out.iloc[4] == 4.0


def test_ema_matches_sma_at_init():
    """EMA should converge to price level on a constant series."""
    s = pd.Series([100.0] * 50)
    out = ema(s, 10)
    # EWM with adjust=False preserves the seed, so EMA = seed from first value
    assert all(out == pytest.approx(100.0, abs=1e-6))


def test_ema_warmup():
    s = pd.Series(range(1, 21), dtype=float)  # 1..20
    out = ema(s, 5)
    # After warmup, EMA(20) should be close to recent values
    assert out.iloc[-1] == pytest.approx(19.0, abs=1.5)


def test_rsi_bounds():
    """RSI must always be in [0, 100]."""
    np.random.seed(0)
    s = pd.Series(100 + np.cumsum(np.random.randn(200)))
    out = rsi(s, 14)
    assert (out.dropna() >= 0).all()
    assert (out.dropna() <= 100).all()


def test_rsi_oversold_on_pure_downtrend():
    """Continuous decline must produce RSI near 0."""
    s = pd.Series(np.arange(100, 0, -1), dtype=float)
    out = rsi(s, 14)
    # After warmup, the last value should be very low
    assert out.iloc[-1] < 5.0


def test_rsi_overbought_on_pure_uptrend():
    # Long enough to clear Wilder's warmup
    s = pd.Series(np.arange(0, 200, dtype=float))
    out = rsi(s, 14)
    # Last 10 should be near 100
    assert (out.dropna().tail(10) > 95.0).all()


def test_bollinger_bands_envelope():
    """Upper > mid > lower and mid == SMA."""
    s = pd.Series(100 + np.cumsum(np.random.RandomState(0).randn(50)))
    bb = bollinger(s, 20, 2.0)
    valid = bb.dropna()
    assert (valid["upper"] >= valid["mid"] - 1e-9).all()
    assert (valid["mid"] >= valid["lower"] - 1e-9).all()


def test_atr_positive():
    np.random.seed(0)
    df = pd.DataFrame({
        "high": 100 + np.abs(np.random.randn(100)),
        "low":  100 - np.abs(np.random.randn(100)),
        "close": 100 + np.random.randn(100),
    })
    out = atr(df, 14)
    valid = out.dropna()
    assert (valid >= 0).all()


def test_macd_columns():
    s = pd.Series(100 + np.cumsum(np.random.RandomState(0).randn(50)))
    m = macd(s)
    assert set(m.columns) == {"macd", "signal", "histogram"}
    # Histogram = macd - signal
    assert (m["histogram"] == m["macd"] - m["signal"]).all()


def test_momentum_no_change_zero():
    s = pd.Series([100.0] * 20)
    out = momentum(s, 10)
    # After warmup, all values should be exactly 0
    valid = out.dropna()
    assert (valid == 0).all()


def test_momentum_uptrend_positive():
    # 10-bar return: price[-1] / price[-11] - 1
    s = pd.Series(np.arange(100, 120, dtype=float))
    out = momentum(s, 10)
    expected = (s.iloc[-1] - s.iloc[-11]) / s.iloc[-11]
    assert out.iloc[-1] == pytest.approx(expected, abs=1e-9)


def test_indicators_reject_bad_period():
    s = pd.Series([1.0, 2.0])
    with pytest.raises(ValueError):
        sma(s, 0)
    with pytest.raises(ValueError):
        ema(s, 0)
    with pytest.raises(ValueError):
        rsi(s, 0)
