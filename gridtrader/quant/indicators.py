"""Technical indicators.

All indicators accept a pandas Series of close prices (or a DataFrame
containing 'close' / 'high' / 'low' columns) and return a Series.

These are pure functions — no side effects, easy to test, and work
identically for live trading and backtesting.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (Wilder's alpha = 1/period)."""
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    if period < 1:
        raise ValueError("period must be >= 1")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)  # neutral when undefined


def bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands. Returns DataFrame with mid, upper, lower."""
    if period < 1:
        raise ValueError("period must be >= 1")
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame({
        "mid": mid,
        "upper": mid + num_std * std,
        "lower": mid - num_std * std,
    })


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range. df must have high, low, close columns."""
    if period < 1:
        raise ValueError("period must be >= 1")
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD. Returns DataFrame with macd, signal, histogram."""
    if fast < 1 or slow < 1 or signal < 1:
        raise ValueError("periods must be >= 1")
    if fast >= slow:
        raise ValueError("fast must be < slow")
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": macd_line - signal_line,
    })


def momentum(series: pd.Series, period: int = 10) -> pd.Series:
    """Price momentum = (price / price_N_bars_ago) - 1."""
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.pct_change(periods=period)
