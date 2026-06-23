"""Sweep RSI threshold pairs on 60d 5m BTCUSDT data.

Goal: find the (oversold, overbought) combo that maximizes Net P&L
under live conditions (5x leverage, 25 USDT margin, 1% SL, 1.5% TP,
0.04% taker fee per side, 0.02% slippage per side).

Output: ranked table so we can pick frequency that actually pays.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CSV = Path("data/cache/BTCUSDT_5m_60d.csv")

# Live trading constants
MARGIN_USDT = 25.0
LEVERAGE = 10
NOTIONAL = MARGIN_USDT * LEVERAGE  # 250 USDT
SL_PCT = 0.006   # 0.6%
TP_PCT = 0.009   # 0.9%
TAKER_FEE = 0.0004  # 0.04% per side
SLIPPAGE = 0.0002   # 0.02% per side
COST_PER_ROUND_TRIP = (TAKER_FEE + SLIPPAGE) * 2 * NOTIONAL  # ~0.15 USDT


def compute_rsi(close: pd.Series, period: int = 7) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / down.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def simulate(df: pd.DataFrame, oversold: float, overbought: float) -> dict:
    """One-position-at-a-time simulator. Enter on RSI extreme, exit at SL/TP."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    rsi = df["rsi"].values

    pnl_usdt = 0.0
    wins = 0
    losses = 0
    trades = 0
    position = None  # ("LONG"|"SHORT", entry_price, sl, tp)
    bar_in_pos = 0
    max_bars_in_pos = 288  # 24h cap

    for i in range(len(closes)):
        c = closes[i]
        h = highs[i]
        l = lows[i]
        r = rsi[i]

        if position is None:
            if pd.isna(r):
                continue
            if r < oversold:
                sl = c * (1 - SL_PCT)
                tp = c * (1 + TP_PCT)
                position = ("LONG", c, sl, tp)
                bar_in_pos = 0
            elif r > overbought:
                sl = c * (1 + SL_PCT)
                tp = c * (1 - TP_PCT)
                position = ("SHORT", c, sl, tp)
                bar_in_pos = 0
        else:
            side, entry, sl, tp = position
            bar_in_pos += 1
            exit_price = None
            outcome = None

            if side == "LONG":
                if l <= sl:
                    exit_price = sl
                    outcome = "loss"
                elif h >= tp:
                    exit_price = tp
                    outcome = "win"
            else:  # SHORT
                if h >= sl:
                    exit_price = sl
                    outcome = "loss"
                elif l <= tp:
                    exit_price = tp
                    outcome = "win"

            if exit_price is None and bar_in_pos >= max_bars_in_pos:
                exit_price = c
                outcome = "timeout"

            if exit_price is not None:
                if side == "LONG":
                    gross = (exit_price - entry) / entry * NOTIONAL
                else:
                    gross = (entry - exit_price) / entry * NOTIONAL
                net = gross - COST_PER_ROUND_TRIP
                pnl_usdt += net
                trades += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                position = None

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades if trades else 0.0,
        "pnl_usdt": pnl_usdt,
        "pnl_per_trade": pnl_usdt / trades if trades else 0.0,
        "trades_per_day": trades / (len(closes) * 5 / 60 / 24),
    }


def main():
    df = pd.read_csv(CSV)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["rsi"] = compute_rsi(df["close"], period=7)
    days = len(df) * 5 / 60 / 24
    print(f"Loaded {len(df)} bars ({days:.1f} days) of BTCUSDT 5m")
    print(f"Constants: {MARGIN_USDT} USDT margin @ {LEVERAGE}x = {NOTIONAL} notional")
    print(f"  SL/TP: {SL_PCT*100:.1f}% / {TP_PCT*100:.1f}%")
    print(f"  Cost per round-trip: {COST_PER_ROUND_TRIP:.4f} USDT")
    print()

    # Threshold sweeps: lower oversold = harder to trigger = lower freq.
    # Symmetric pairs (RSI is symmetric around 50).
    configs = [
        # (oversold, overbought, label)
        (10, 90, "ultra-strict (current-but-stricter)"),
        (15, 85, "very-strict"),
        (20, 80, "current v3 LIVE"),
        (25, 75, "loose"),
        (30, 70, "very-loose"),
        (35, 65, "aggressive"),
        (40, 60, "ultra-aggressive (almost any swing)"),
    ]

    print(f"{'config':<42} {'trades':>7} {'tr/day':>7} {'WR%':>6} {'pnl$':>9} {'pnl/tr$':>9}")
    print("-" * 88)
    results = []
    for os_, ob, label in configs:
        r = simulate(df, os_, ob)
        results.append((os_, ob, label, r))
        print(f"{label:<42} {r['trades']:>7d} {r['trades_per_day']:>7.2f} "
              f"{r['win_rate']*100:>5.1f}% {r['pnl_usdt']:>+8.2f} {r['pnl_per_trade']:>+8.3f}")
    print("=" * 88)

    # Pick winner
    profitable = [x for x in results if x[3]["pnl_usdt"] > 0]
    if profitable:
        best = max(profitable, key=lambda x: x[3]["pnl_usdt"])
        print(f"\nBEST P&L: oversold={best[0]} overbought={best[1]} ({best[2]})")
        print(f"  +{best[3]['pnl_usdt']:.2f} USDT over {best[3]['trades']} trades")
        print(f"  = {best[3]['pnl_usdt']/days:.3f} USDT/day average")
    else:
        print("\nNO PROFITABLE CONFIG. All thresholds net loss.")
        # Pick least-bad
        best = max(results, key=lambda x: x[3]["pnl_usdt"])
        print(f"Least-bad: oversold={best[0]} overbought={best[1]} ({best[2]})")
        print(f"  {best[3]['pnl_usdt']:+.2f} USDT, {best[3]['trades']} trades")


if __name__ == "__main__":
    main()
