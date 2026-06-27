"""Compare all strategies on the same historical data and print a ranked table.

Usage:
    python scripts/run_backtest.py --csv data/cache/BTCUSDT_1h_30d.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.backtest import Backtester, format_metrics
from gridtrader.quant.strategies import STRATEGIES, get_strategy


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Standard columns from Binance klines: open_time, open, high, low, close, volume, ... / Binance K线标准列：open_time, open, high, low, close, volume, ...
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True, help="CSV with open/high/low/close/volume + ts index")
    p.add_argument("--initial-cash", type=float, default=29.0)
    p.add_argument("--commission-bps", type=float, default=10.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--position-size-pct", type=float, default=0.95)
    args = p.parse_args()

    df = load_csv(args.csv)
    symbol = Path(args.csv).stem.split("_")[0]
    print(f"Loaded {len(df)} bars of {symbol}")
    print(f"  range : {df.index[0]} -> {df.index[-1]}")
    print(f"  close : {df['close'].iloc[0]:.2f} -> {df['close'].iloc[-1]:.2f}  ({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.2f}%)")
    print()

    results = []
    for name in STRATEGIES:
        st = get_strategy(name)
        bt = Backtester(
            strategy=st,
            initial_cash=args.initial_cash,
            commission_bps=args.commission_bps,
            slippage_bps=args.slippage_bps,
            position_size_pct=args.position_size_pct,
        )
        res = bt.run(df, symbol=symbol)
        results.append((name, res))

    # Sort by total return / 按总收益率排序
    results.sort(key=lambda x: x[1].metrics["total_return"], reverse=True)

    print("=" * 88)
    print(f"{'strategy':<12} {'return%':>8} {'final$':>10} {'trades':>7} {'win%':>6} {'sharpe':>7} {'maxDD%':>8} {'PF':>6}")
    print("-" * 88)
    for name, res in results:
        m = res.metrics
        pf = m["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{name:<12} {m['total_return']*100:>7.2f}% {m['final_equity']:>10.2f} "
              f"{m['n_trades']:>7d} {m['win_rate']*100:>5.1f}% "
              f"{m['sharpe']:>7.2f} {m['max_drawdown']*100:>7.2f}% {pf_s:>6s}")
    print("=" * 88)
    print()

    # Detail for the best / 最优策略详情
    best_name, best = results[0]
    print(f"--- Best: {best_name} ---")
    print(format_metrics(best.metrics))
    print()
    if best.trades:
        print("Last 5 trades:")
        for t in best.trades[-5:]:
            ts = t.ts.strftime("%Y-%m-%d %H:%M") if hasattr(t.ts, "strftime") else t.ts
            print(f"  {ts}  {t.side:<4s}  qty={t.qty:.6f}  price={t.price:.2f}  pnl={t.pnl:+.2f}  reason={t.reason[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
