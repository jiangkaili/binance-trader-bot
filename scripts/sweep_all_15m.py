"""Compare ALL 4 strategies on 15m BTCUSDT data."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.backtest import Backtester
from gridtrader.quant.strategies import (
    MaCrossStrategy, BollingerStrategy, RsiRevertStrategy, MomentumStrategy,
)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> int:
    df = load_csv("data/cache/BTCUSDT_15m_60d.csv")
    print(f"Loaded {len(df)} bars of BTCUSDT 15m (BTC -13% over 60d)")
    print()

    strategies = [
        ("ma_cross",       lambda: MaCrossStrategy(fast=12, slow=26)),
        ("ma_cross short", lambda: MaCrossStrategy(fast=12, slow=26)),  # we'll allow short
        ("bollinger",      lambda: BollingerStrategy(period=20, num_std=2.0)),
        ("rsi_revert",     lambda: RsiRevertStrategy(period=14, oversold=30, overbought=70)),
        ("momentum",       lambda: MomentumStrategy(period=20, threshold=0.02, long_only=False)),
    ]

    print(f"{'strategy':<18} {'allow_sh':<9} {'return%':>8} {'final$':>8} {'trades':>7} {'win%':>6} {'sharpe':>7} {'maxDD%':>7}")
    print("-" * 70)
    for name, factory in strategies:
        st = factory()
        # First run: long-only (default)
        bt = Backtester(
            strategy=st, initial_cash=5.0, commission_bps=10, slippage_bps=2,
            position_size_pct=0.95, allow_short=False,
        )
        res = bt.run(df, "BTCUSDT")
        m = res.metrics
        print(f"{name:<18} {'no':<9} {m['total_return']*100:>+7.2f}% {m['final_equity']:>7.2f} {m['n_trades']:>7d} {m['win_rate']*100:>5.1f}% {m['sharpe']:>+6.2f} {m['max_drawdown']*100:>+6.2f}%")

    print()
    print("=== Now with allow_short=True (can profit from downtrends) ===")
    print()
    print(f"{'strategy':<18} {'allow_sh':<9} {'return%':>8} {'final$':>8} {'trades':>7} {'win%':>6} {'sharpe':>7} {'maxDD%':>7}")
    print("-" * 70)
    for name, factory in strategies:
        st = factory()
        bt = Backtester(
            strategy=st, initial_cash=5.0, commission_bps=10, slippage_bps=2,
            position_size_pct=0.95, allow_short=True,
        )
        res = bt.run(df, "BTCUSDT")
        m = res.metrics
        print(f"{name:<18} {'yes':<9} {m['total_return']*100:>+7.2f}% {m['final_equity']:>7.2f} {m['n_trades']:>7d} {m['win_rate']*100:>5.1f}% {m['sharpe']:>+6.2f} {m['max_drawdown']*100:>+6.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
