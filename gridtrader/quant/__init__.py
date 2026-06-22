"""Quant extension package for the gridtrader framework.

This package adds:
- Configuration management (env vars + YAML)
- Technical indicators (SMA, EMA, RSI, Bollinger, ATR, MACD)
- Multi-strategy library (MA cross, Bollinger, RSI mean-revert, momentum)
- Backtesting engine with metrics (Sharpe, max drawdown, win rate, etc.)
- Risk management (position, daily loss, max open orders)
- Trade persistence (SQLite)
- Paper trading mode (simulated fills against live tick stream)
"""
__version__ = "0.1.0"
