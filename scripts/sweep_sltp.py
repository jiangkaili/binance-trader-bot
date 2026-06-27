"""Parameter sweep for RSI mean-reversion with SL/TP + leverage.

Models the live trader behavior accurately on 60d 5m BTC data.
Usage: python scripts/sweep_sltp.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def rsi(close: pd.Series, period: int = 7) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period, min_periods=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    return dx.ewm(alpha=1 / period, min_periods=period).mean()


def simulate(
    df: pd.DataFrame,
    rsi_buy: float,
    rsi_sell: float,
    sl_pct: float,
    tp_pct: float,
    leverage: int,
    margin_usdt: float = 16.0,
    fee_bps: float = 6.0,
    cooldown_bars: int = 12,
    adx_threshold: float = 25.0,
    use_adx: bool = True,
) -> dict:
    fee_rate = fee_bps / 10000
    cash = margin_usdt
    position = 0  # 0=flat, 1=long, -1=short / 0=空仓, 1=做多, -1=做空
    entry_price = 0.0
    qty = 0.0
    last_trade_bar = -999
    trades = []

    for i in range(50, len(df)):
        bar = df.iloc[i]
        rsi_val = bar["rsi"]
        adx_val = bar["adx"]

        # Check SL/TP for open position / 检查持仓的止损/止盈
        # In futures: open deducts entry fee, close adds PnL minus close fee.
        # Margin is implicit (leverage = notional / cash).
        # 在期货中：开仓扣除入场手续费，平仓加PnL减平仓手续费。保证金是隐式的（杠杆 = 名义价值 / 现金）。
        if position != 0:
            close_fee_mult = qty * fee_rate
            if position == 1:  # long / 做多
                sl_price = entry_price * (1 - sl_pct)
                tp_price = entry_price * (1 + tp_pct)
                if bar["low"] <= sl_price:
                    pnl = (sl_price - entry_price) * qty - sl_price * close_fee_mult
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "long", "reason": "SL"})
                    position = 0
                    last_trade_bar = i
                elif bar["high"] >= tp_price:
                    pnl = (tp_price - entry_price) * qty - tp_price * close_fee_mult
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "long", "reason": "TP"})
                    position = 0
                    last_trade_bar = i
            elif position == -1:  # short / 做空
                sl_price = entry_price * (1 + sl_pct)
                tp_price = entry_price * (1 - tp_pct)
                if bar["high"] >= sl_price:
                    pnl = (entry_price - sl_price) * qty - sl_price * close_fee_mult
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "short", "reason": "SL"})
                    position = 0
                    last_trade_bar = i
                elif bar["low"] <= tp_price:
                    pnl = (entry_price - tp_price) * qty - tp_price * close_fee_mult
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "short", "reason": "TP"})
                    position = 0
                    last_trade_bar = i

        # Check for new entry / 检查新入场
        if position == 0 and (i - last_trade_bar) >= cooldown_bars:
            if pd.isna(rsi_val) or pd.isna(adx_val):
                continue
            if use_adx and adx_val > adx_threshold:
                continue

            notional = cash * leverage
            if notional <= 0:
                break  # blown account / 账户爆仓

            if rsi_val < rsi_buy:
                entry_price = bar["close"]
                qty = notional / entry_price
                fee = entry_price * qty * fee_rate
                cash -= fee
                position = 1
            elif rsi_val > rsi_sell:
                entry_price = bar["close"]
                qty = notional / entry_price
                fee = entry_price * qty * fee_rate
                cash -= fee
                position = -1

    # Close remaining position / 平掉剩余仓位
    if position != 0:
        last_close = float(df.iloc[-1]["close"])
        close_fee_mult = qty * fee_rate
        if position == 1:
            pnl = (last_close - entry_price) * qty - last_close * close_fee_mult
        else:
            pnl = (entry_price - last_close) * qty - last_close * close_fee_mult
        cash += pnl
        trades.append({"pnl": pnl, "side": "long" if position == 1 else "short", "reason": "EOD"})

    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    n_trades = len(trades)
    win_rate = len(wins) / n_trades if n_trades > 0 else 0
    avg_win = float(np.mean([t["pnl"] for t in wins])) if wins else 0
    avg_loss = float(np.mean([t["pnl"] for t in losses])) if losses else 0
    tp_count = len([t for t in trades if t["reason"] == "TP"])
    sl_count = len([t for t in trades if t["reason"] == "SL"])

    return {
        "total_pnl": total_pnl,
        "final_cash": cash,
        "n_trades": n_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "tp_count": tp_count,
        "sl_count": sl_count,
        "trades": trades,
    }


def main() -> int:
    csv = Path(__file__).resolve().parent.parent / "data/cache/BTCUSDT_5m_60d.csv"
    df = pd.read_csv(csv)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    print(f"Loaded {len(df)} bars")
    print(f"Range: {df.index[0]} -> {df.index[-1]}")
    print(f"Price: {df['close'].iloc[0]:.0f} -> {df['close'].iloc[-1]:.0f} "
          f"({(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:+.1f}%)")

    df["rsi"] = rsi(df["close"], 7)
    df["adx"] = adx(df["high"], df["low"], df["close"], 14)

    configs = [
        # (label, rsi_buy, rsi_sell, sl_pct, tp_pct, leverage, adx_thresh, use_adx) / (标签, RSI买入, RSI卖出, 止损百分比, 止盈百分比, 杠杆, ADX阈值, 是否使用ADX)
        ("v5 current",              12, 88, 0.005, 0.010, 10, 25, True),
        ("v5 5x lev",               12, 88, 0.005, 0.010,  5, 25, True),
        ("SL1.0 TP2.0 10x",         12, 88, 0.010, 0.020, 10, 25, True),
        ("SL1.0 TP2.0 5x",          12, 88, 0.010, 0.020,  5, 25, True),
        ("SL1.5 TP3.0 10x",         12, 88, 0.015, 0.030, 10, 25, True),
        ("SL1.5 TP3.0 5x",          12, 88, 0.015, 0.030,  5, 25, True),
        ("SL1.0 TP2.0 5x noADX",    12, 88, 0.010, 0.020,  5, 25, False),
        ("SL0.8 TP1.6 5x",          12, 88, 0.008, 0.016,  5, 25, True),
        ("SL1.2 TP2.4 5x",          12, 88, 0.012, 0.024,  5, 25, True),
        ("SL1.0 TP2.0 5x ADX20",    12, 88, 0.010, 0.020,  5, 20, True),
        ("SL1.0 TP2.0 5x ADX30",    12, 88, 0.010, 0.020,  5, 30, True),
        ("SL1.5 TP2.5 5x",          12, 88, 0.015, 0.025,  5, 25, True),
        ("SL1.0 TP1.5 5x",          12, 88, 0.010, 0.015,  5, 25, True),
        ("SL2.0 TP4.0 5x",          12, 88, 0.020, 0.040,  5, 25, True),
        ("SL1.0 TP2.0 5x RSI15/85", 15, 85, 0.010, 0.020,  5, 25, True),
        ("SL1.0 TP2.0 5x RSI10/90", 10, 90, 0.010, 0.020,  5, 25, True),
    ]

    print("\n" + "=" * 120)
    print(f"{'Config':<28} {'PnL':>8} {'Final':>8} {'Trades':>7} {'Win%':>6} "
          f"{'TP':>4} {'SL':>4} {'AvgWin':>8} {'AvgLoss':>9} {'R:R':>6}")
    print("-" * 120)

    results = []
    for label, rb, rs, sl, tp, lev, adx_th, use_adx in configs:
        r = simulate(df, rb, rs, sl, tp, lev, margin_usdt=16.0,
                     adx_threshold=adx_th, use_adx=use_adx)
        rr = abs(r["avg_win"] / r["avg_loss"]) if r["avg_loss"] != 0 else float("inf")
        print(f"{label:<28} {r['total_pnl']:>8.2f} {r['final_cash']:>8.2f} "
              f"{r['n_trades']:>7d} {r['win_rate'] * 100:>5.1f}% {r['tp_count']:>4d} "
              f"{r['sl_count']:>4d} {r['avg_win']:>8.2f} {r['avg_loss']:>9.2f} {rr:>6.2f}")
        results.append((label, r))

    results.sort(key=lambda x: x[1]["total_pnl"], reverse=True)
    print("\n" + "=" * 120)
    print("TOP 5 by total PnL:")
    for label, r in results[:5]:
        rr = abs(r["avg_win"] / r["avg_loss"]) if r["avg_loss"] != 0 else float("inf")
        print(f"  {label:<28} PnL={r['total_pnl']:>8.2f}  Trades={r['n_trades']:>4d}  "
              f"WR={r['win_rate'] * 100:.1f}%  TP/SL={r['tp_count']}/{r['sl_count']}  "
              f"R:R=1:{rr:.2f}")

    print("\nBOTTOM 3:")
    for label, r in results[-3:]:
        rr = abs(r["avg_win"] / r["avg_loss"]) if r["avg_loss"] != 0 else float("inf")
        print(f"  {label:<28} PnL={r['total_pnl']:>8.2f}  Trades={r['n_trades']:>4d}  "
              f"WR={r['win_rate'] * 100:.1f}%  TP/SL={r['tp_count']}/{r['sl_count']}  "
              f"R:R=1:{rr:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
