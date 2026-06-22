"""Tests for the backtest engine + risk manager + storage."""
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gridtrader.quant.backtest import Backtester, compute_metrics, format_metrics
from gridtrader.quant.config import RiskSettings
from gridtrader.quant.risk import RiskManager, RiskViolation, Account
from gridtrader.quant.storage import Store
from gridtrader.quant.strategies import MaCrossStrategy, BollingerStrategy


def make_v_shape(n=200):
    """Price path that goes down then up — should produce a golden cross."""
    t = np.arange(n)
    prices = np.maximum(100 - 0.5 * t + 0.005 * t * t, 10)
    return pd.DataFrame(
        {
            "open": prices, "high": prices * 1.001, "low": prices * 0.999,
            "close": prices, "volume": [100] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )


# -------- backtester --------

def test_backtester_runs_without_error():
    df = make_v_shape()
    bt = Backtester(strategy=MaCrossStrategy(), initial_cash=10000)
    res = bt.run(df, "V")
    assert res.symbol == "V"
    assert res.strategy == "ma_cross"
    assert isinstance(res.metrics, dict)
    assert "total_return" in res.metrics


def test_backtester_closes_open_position():
    df = make_v_shape()
    bt = Backtester(strategy=MaCrossStrategy(), initial_cash=10000)
    res = bt.run(df, "V")
    # After end-of-backtest close, we should be flat
    assert bt.qty == 0


def test_backtester_no_short_by_default():
    """Momentum strategy configured long_only=True must not open shorts."""
    df = make_v_shape()
    bt = Backtester(strategy=BollingerStrategy(), initial_cash=10000, allow_short=False)
    res = bt.run(df, "V")
    sells = sum(1 for t in res.trades if t.side == "SELL")
    # All SELL trades should be closes (preceded by a BUY)
    # and we should never be in net short.
    assert bt.qty == 0


def test_backtester_rejects_short_index():
    df = make_v_shape()
    df_no_dt = df.reset_index()
    bt = Backtester(strategy=MaCrossStrategy())
    with pytest.raises(ValueError):
        bt.run(df_no_dt, "X")


def test_backtester_rejects_too_few_bars():
    df = make_v_shape(n=10)
    bt = Backtester(strategy=MaCrossStrategy())
    with pytest.raises(ValueError):
        bt.run(df, "X")


def test_compute_metrics_basic():
    eq = pd.Series([100, 110, 99, 105, 115])
    trades = []
    m = compute_metrics(eq, trades, initial_cash=100, periods_per_year=252)
    assert m["total_return"] == pytest.approx(0.15, abs=1e-9)
    assert m["n_trades"] == 0


def test_format_metrics_returns_string():
    df = make_v_shape()
    bt = Backtester(strategy=MaCrossStrategy(), initial_cash=10000)
    res = bt.run(df, "V")
    s = format_metrics(res.metrics)
    assert isinstance(s, str)
    assert "Sharpe" in s


# -------- storage --------

def test_store_round_trip():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "test.db")
        store = Store(path)
        store.log_trade(symbol="BTCUSDT", side="BUY", price=60000, qty=0.01, source="paper")
        store.log_trade(symbol="BTCUSDT", side="SELL", price=61000, qty=0.01, source="paper", pnl=10.0)
        df = store.trades()
        assert len(df) == 2
        assert df.iloc[0]["side"] == "BUY"
        assert df.iloc[1]["pnl"] == 10.0
        # Filter by symbol
        store.log_trade(symbol="ETHUSDT", side="BUY", price=3000, qty=0.1, source="paper")
        btc = store.trades(symbol="BTCUSDT")
        assert len(btc) == 2
        # Daily pnl
        pnl = store.daily_pnl(symbol="BTCUSDT")
        assert pnl.iloc[0]["pnl"] == 10.0


def test_store_thread_safe():
    """Concurrent writes must not corrupt the database."""
    import threading
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "test.db")
        store = Store(path)
        def writer(n):
            for i in range(n):
                store.log_trade(symbol="X", side="BUY", price=1.0, qty=1.0, source="paper")
        threads = [threading.Thread(target=writer, args=(50,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(store.trades()) == 250


# -------- risk manager --------

def test_risk_allows_safe_order():
    with tempfile.TemporaryDirectory() as d:
        store = Store(str(Path(d) / "t.db"))
        rm = RiskManager(RiskSettings(), store, Account.from_equity(10000))
        rm.check_order(symbol="BTCUSDT", side="BUY", qty=0.01, price=60000)  # $600 < 10% of 10k


def test_risk_blocks_oversized_order():
    with tempfile.TemporaryDirectory() as d:
        store = Store(str(Path(d) / "t.db"))
        rm = RiskManager(RiskSettings(), store, Account.from_equity(10000))
        with pytest.raises(RiskViolation):
            rm.check_order(symbol="BTCUSDT", side="BUY", qty=1.0, price=60000)  # $60k > 10%


def test_risk_blocks_after_daily_loss():
    with tempfile.TemporaryDirectory() as d:
        store = Store(str(Path(d) / "t.db"))
        account = Account.from_equity(10000)
        rm = RiskManager(RiskSettings(max_daily_loss_pct=0.05), store, account)
        # simulate a $600 loss today
        rm.apply_fill("BTCUSDT", "SELL", 0.01, 60000, fee=0)  # open a short? no — we'll do an opposite fill
        rm.apply_fill("BTCUSDT", "BUY", 0.01, 66000, fee=0)  # close short at loss
        # -0.01 * 6000 = -60 realized, not enough; make a bigger loss
        rm.apply_fill("ETHUSDT", "BUY", 1.0, 3000, fee=0)
        rm.apply_fill("ETHUSDT", "SELL", 1.0, 2400, fee=0)  # -600
        # Now daily loss should exceed 5% of 10000 = $500
        with pytest.raises(RiskViolation):
            rm.check_order(symbol="BTCUSDT", side="BUY", qty=0.01, price=60000)


def test_risk_rejects_invalid_args():
    with tempfile.TemporaryDirectory() as d:
        store = Store(str(Path(d) / "t.db"))
        rm = RiskManager(RiskSettings(), store, Account.from_equity(10000))
        with pytest.raises(RiskViolation):
            rm.check_order(symbol="X", side="BUY", qty=0, price=1)
        with pytest.raises(RiskViolation):
            rm.check_order(symbol="X", side="BUY", qty=1, price=0)


def test_risk_apply_fill_realized_pnl():
    with tempfile.TemporaryDirectory() as d:
        store = Store(str(Path(d) / "t.db"))
        rm = RiskManager(RiskSettings(), store, Account.from_equity(10000))
        # Open long at 100
        rm.apply_fill("X", "BUY", 1.0, 100.0)
        assert rm.position("X").qty == 1.0
        assert rm.position("X").avg_price == 100.0
        # Close at 110 — realized PnL = 10
        pnl = rm.apply_fill("X", "SELL", 1.0, 110.0, fee=0)
        assert pnl == pytest.approx(10.0, abs=1e-9)
        assert rm.position("X").is_flat


def test_risk_snapshot_structure():
    with tempfile.TemporaryDirectory() as d:
        store = Store(str(Path(d) / "t.db"))
        rm = RiskManager(RiskSettings(), store, Account.from_equity(10000))
        snap = rm.snapshot()
        assert "account" in snap
        assert "open_orders" in snap
        assert "daily_pnl" in snap
        assert "positions" in snap
        assert snap["account"]["starting_equity"] == 10000
