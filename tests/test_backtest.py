"""Tests for gridtrader.quant.backtest — Backtester + RsiRevertStrategy.

Covers:
  - A basic run on synthetic V-shape OHLCV data returns a well-formed
    BacktestResult (trades list, metrics dict, equity curve).
  - The metrics dict contains the expected performance keys.
  - final_equity is positive, finite, and within a sane band of the
    initial cash (one round-trip can't wipe out or 10x the account).
  - The V-shape triggers at least one BUY (oversold) and one SELL.
"""
# gridtrader.quant.backtest 测试 — Backtester + RsiRevertStrategy。
# 覆盖: 在合成V形OHLCV数据上基本运行, 返回结构完整的BacktestResult
#       (交易列表、指标字典、权益曲线); 指标字典含预期绩效键;
#       最终权益为正、有限, 且在初始资金的合理区间内(一次往返不可能归零或翻10倍);
#       V形数据至少触发一次买入(超卖)和一次卖出。
import math

import numpy as np
import pandas as pd

from gridtrader.quant.backtest import Backtester, BacktestResult
from gridtrader.quant.strategies import RsiRevertStrategy


def make_v_shape(n=400):
    """V-shape close path: gentle downtrend then uptrend.

    A sustained downtrend drives RSI toward 0 (oversold → BUY); the
    subsequent uptrend drives RSI above the overbought threshold (→ SELL),
    producing at least one complete round-trip.
    """
    # V形收盘价路径: 温和下跌后上涨。持续下跌使RSI趋近0(超卖→买入);
    # 随后上涨使RSI升破超买阈值(→卖出), 产生至少一次完整往返。
    half = n // 2
    down = np.linspace(100.0, 80.0, half)
    up = np.linspace(80.0, 110.0, n - half)
    close = np.concatenate([down, up])
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": [1000.0] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )


def _run():
    # shared setup: strategy + backtester on V-shape data / 共享设置: 策略+回测器作用于V形数据
    df = make_v_shape()
    strat = RsiRevertStrategy(period=7, oversold=30.0, overbought=70.0)
    return Backtester(strategy=strat, initial_cash=10_000.0).run(df, symbol="BTCUSDT")


def test_backtest_returns_well_formed_result():
    result = _run()
    # correct type and identifying fields / 类型正确及标识字段
    assert isinstance(result, BacktestResult)
    assert result.symbol == "BTCUSDT"
    assert result.strategy == "rsi_revert"

    # trades is a list / trades为列表
    assert isinstance(result.trades, list)

    # equity curve is a Series with a DatetimeIndex / 权益曲线为带DatetimeIndex的Series
    assert isinstance(result.equity_curve, pd.Series)
    assert len(result.equity_curve) > 0
    assert isinstance(result.equity_curve.index, pd.DatetimeIndex)


def test_metrics_have_expected_keys():
    result = _run()
    expected = {
        "total_return", "cagr", "sharpe", "max_drawdown",
        "n_bars", "n_trades", "win_rate", "avg_win",
        "avg_loss", "profit_factor", "final_equity",
    }
    assert expected.issubset(result.metrics.keys())
    # n_bars matches the equity curve length / n_bars与权益曲线长度一致
    assert result.metrics["n_bars"] == len(result.equity_curve)


def test_final_equity_reasonable():
    result = _run()
    # positive and finite / 为正且有限
    assert math.isfinite(result.final_equity)
    assert result.final_equity > 0
    # one round-trip can't wipe out (> -50%) or 10x the account / 一次往返不可能亏损过半(>-50%)或翻10倍
    assert 0.5 * result.initial_cash < result.final_equity < 2.0 * result.initial_cash
    # metrics final_equity agrees with the result field / 指标final_equity与结果字段一致
    assert result.metrics["final_equity"] == result.final_equity


def test_v_shape_triggers_buy_and_sell():
    # downtrend → oversold BUY; recovery → overbought SELL / 下跌→超卖买入; 反弹→超买卖出
    result = _run()
    assert len(result.trades) >= 2  # at least one open + one close / 至少一次开仓+一次平仓
    sides = {t.side for t in result.trades}
    assert "BUY" in sides
    assert "SELL" in sides
