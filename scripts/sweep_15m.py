"""Parameter sweep for ma_cross on 15m BTCUSDT data.

Runs ma_cross with several fast/slow combos and reports metrics.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.backtest import Backtester
from gridtrader.quant.strategies import MaCrossStrategy


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> int:
    csv = "data/cache/BTCUSDT_15m_60d.csv"
    df = load_csv(csv)
    print(f"Loaded {len(df)} bars of BTCUSDT 15m")
    print(f"  range: {df.index[0]} -> {df.index[-1]}")
    print(f"  close: {df['close'].iloc[0]:.2f} -> {df['close'].iloc[-1]:.2f}  ({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.2f}%)")
    print()

    # Try a range of fast/slow pairs that make sense for 15m scalping
    # 15m × 8 bars = 2 hours (fast)
    # 15m × 21 bars = 5.25 hours (slow)
    pairs = [
        (5, 13),    # 1.25h / 3.25h  — ultra short
        (5, 20),    # 1.25h / 5h
        (8, 21),    # 2h   / 5.25h
        (8, 34),    # 2h   / 8.5h
        (12, 26),   # 3h   / 6.5h  — default
        (13, 34),   # 3.25h/ 8.5h
        (20, 50),   # 5h   / 12.5h — slower
    ]

    results = []
    for fast, slow in pairs:
        st = MaCrossStrategy(fast=fast, slow=slow)
        bt = Backtester(
            strategy=st,
            initial_cash=5.0,
            commission_bps=10,
            slippage_bps=2,
            position_size_pct=0.95,
        )
        res = bt.run(df, "BTCUSDT")
        results.append((fast, slow, res))
        m = res.metrics
        pf = m["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"  fast={fast:>2d} slow={slow:>2d}  "
              f"return={m['total_return']*100:+7.2f}%  "
              f"final=${m['final_equity']:.2f}  "
              f"trades={m['n_trades']:>3d}  "
              f"win%={m['win_rate']*100:>5.1f}  "
              f"sharpe={m['sharpe']:>5.2f}  "
              f"maxDD={m['max_drawdown']*100:>6.2f}%  "
              f"PF={pf_s:>5s}")

    print()
    # Rank by return
    results.sort(key=lambda r: r[2].metrics["total_return"], reverse=True)
    print("Ranked by total return:")
    for i, (fast, slow, res) in enumerate(results, 1):
        m = res.metrics
        print(f"  {i}. fast={fast} slow={slow}  return={m['total_return']*100:+.2f}%  sharpe={m['sharpe']:+.2f}  trades={m['n_trades']}  maxDD={m['max_drawdown']*100:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
