#!/usr/bin/env python3
"""Multi-timeframe + trailing-stop backtest.

Tests the best RSI 20/80 config across 5m/15m/1h/4h timeframes,
with fixed TP vs trailing stop variants. Goal: find PF > 1.3.
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gridtrader.quant.indicators import rsi, adx


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def simulate(df, sl_pct, tp_pct, leverage, rsi_buy, rsi_sell,
             use_adx=True, adx_threshold=25, cooldown=12,
             margin=16.0, fee_bps=6.0, trailing_pct=None):
    fee_rate = fee_bps / 10000
    r = rsi(df["close"], 7)
    a = adx(df[["high", "low", "close"]], 14)

    cash = margin
    position = 0
    entry_price = 0.0
    qty = 0.0
    best_price = 0.0
    last_trade_bar = -999
    trades = []
    equity_curve = []

    for i in range(50, len(df)):
        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])
        close = float(df.iloc[i]["close"])

        if position != 0:
            close_fee = qty * fee_rate
            if position == 1:
                sl_price = entry_price * (1 - sl_pct)
                if trailing_pct:
                    best_price = max(best_price, high)
                    tp_price = best_price * (1 - trailing_pct)
                    tp_price = max(tp_price, sl_price)
                else:
                    tp_price = entry_price * (1 + tp_pct)
                if low <= sl_price:
                    pnl = (sl_price - entry_price) * qty - sl_price * close_fee
                    cash += pnl; trades.append({"pnl": pnl, "reason": "SL"})
                    position = 0; last_trade_bar = i
                elif trailing_pct and low <= tp_price and tp_price > entry_price:
                    pnl = (tp_price - entry_price) * qty - tp_price * close_fee
                    cash += pnl; trades.append({"pnl": pnl, "reason": "TRAIL"})
                    position = 0; last_trade_bar = i
                elif not trailing_pct and high >= tp_price:
                    pnl = (tp_price - entry_price) * qty - tp_price * close_fee
                    cash += pnl; trades.append({"pnl": pnl, "reason": "TP"})
                    position = 0; last_trade_bar = i
            elif position == -1:
                sl_price = entry_price * (1 + sl_pct)
                if trailing_pct:
                    best_price = min(best_price, low)
                    tp_price = best_price * (1 + trailing_pct)
                    tp_price = min(tp_price, sl_price)
                else:
                    tp_price = entry_price * (1 - tp_pct)
                if high >= sl_price:
                    pnl = (entry_price - sl_price) * qty - sl_price * close_fee
                    cash += pnl; trades.append({"pnl": pnl, "reason": "SL"})
                    position = 0; last_trade_bar = i
                elif trailing_pct and high >= tp_price and tp_price < entry_price:
                    pnl = (entry_price - tp_price) * qty - tp_price * close_fee
                    cash += pnl; trades.append({"pnl": pnl, "reason": "TRAIL"})
                    position = 0; last_trade_bar = i
                elif not trailing_pct and low <= tp_price:
                    pnl = (entry_price - tp_price) * qty - tp_price * close_fee
                    cash += pnl; trades.append({"pnl": pnl, "reason": "TP"})
                    position = 0; last_trade_bar = i

        if position == 0 and (i - last_trade_bar) >= cooldown:
            if pd.isna(r.iloc[i]) or pd.isna(a.iloc[i]):
                pass
            elif use_adx and a.iloc[i] > adx_threshold:
                pass
            elif r.iloc[i] < rsi_buy:
                notional = cash * leverage
                if notional <= 0:
                    break
                entry_price = close; qty = notional / entry_price
                cash -= entry_price * qty * fee_rate
                position = 1; best_price = high
            elif r.iloc[i] > rsi_sell:
                notional = cash * leverage
                if notional <= 0:
                    break
                entry_price = close; qty = notional / entry_price
                cash -= entry_price * qty * fee_rate
                position = -1; best_price = low

        if position == 1:
            mtm = cash + (close - entry_price) * qty
        elif position == -1:
            mtm = cash + (entry_price - close) * qty
        else:
            mtm = cash
        equity_curve.append(mtm)

    if position != 0:
        last_close = float(df.iloc[-1]["close"])
        close_fee = qty * fee_rate
        pnl = ((last_close - entry_price) if position == 1 else (entry_price - last_close)) * qty - last_close * close_fee
        cash += pnl; trades.append({"pnl": pnl, "reason": "EOD"})

    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gp / gl if gl > 0 else float("inf")
    n = len(trades)
    wr = len(wins) / n if n > 0 else 0
    eq = pd.Series(equity_curve)
    peak = eq.expanding().max()
    max_dd = float(((eq - peak) / peak).min()) if len(eq) > 0 else 0.0
    return {"pnl": total_pnl, "n": n, "wr": wr, "pf": pf, "max_dd": max_dd}


def main() -> int:
    base = Path(__file__).resolve().parent.parent / "data" / "cache"
    files = {
        "5m_60d":  "BTCUSDT_5m_60d.csv",
        "15m_60d": "BTCUSDT_15m_60d.csv",
        "1h_60d":  "BTCUSDT_1h_60d.csv",
        "4h_90d":  "BTCUSDT_4h_90d.csv",
    }
    dfs = {}
    for label, fname in files.items():
        p = base / fname
        if p.exists():
            dfs[label] = load_csv(str(p))
            ch = (dfs[label]["close"].iloc[-1] / dfs[label]["close"].iloc[0] - 1) * 100
            print(f"{label}: {len(dfs[label])} bars, {ch:+.1f}%")

    configs = [
        ("RSI20/80 SL1.5 TP3.0",       0.015, 0.030, 5, 20, 80, True,  12, None),
        ("RSI20/80 SL2.0 TP4.0",       0.020, 0.040, 5, 20, 80, True,  12, None),
        ("RSI25/75 SL2.0 TP4.0",       0.020, 0.040, 5, 25, 75, True,  12, None),
        ("RSI30/70 SL2.0 TP4.0",       0.020, 0.040, 5, 30, 70, True,  12, None),
        ("RSI30/70 SL3.0 TP6.0",       0.030, 0.060, 5, 30, 70, True,  12, None),
        ("RSI25/75 SL2.0 TP4.0 noADX", 0.020, 0.040, 5, 25, 75, False, 12, None),
        ("RSI30/70 SL3.0 TP6.0 noADX", 0.030, 0.060, 5, 30, 70, False, 12, None),
        # Trailing stops
        ("RSI20/80 SL2.0 trail2.0%",   0.020, 0, 5, 20, 80, True, 12, 0.020),
        ("RSI25/75 SL2.0 trail2.5%",   0.020, 0, 5, 25, 75, True, 12, 0.025),
        ("RSI30/70 SL2.0 trail3.0%",   0.020, 0, 5, 30, 70, True, 12, 0.030),
        ("RSI30/70 SL3.0 trail3.0%",   0.030, 0, 5, 30, 70, True, 12, 0.030),
        ("RSI30/70 SL3.0 trail4.0%",   0.030, 0, 5, 30, 70, True, 12, 0.040),
        ("RSI25/75 SL3.0 trail3.0 noADX", 0.030, 0, 5, 25, 75, False, 12, 0.030),
        ("RSI30/70 SL3.0 trail4.0 noADX", 0.030, 0, 5, 30, 70, False, 12, 0.040),
    ]

    print("\n" + "=" * 140)
    hdr = f"{'Config':<35}"
    for label in dfs:
        hdr += f" | {label:>18} PnL  N  WR  PF  DD"
    print(hdr)
    print("-" * 140)

    results = {}
    for name, sl, tp, lev, rb, rs, adx_f, cd, trail in configs:
        row = f"{name:<35}"
        for label, df in dfs.items():
            r = simulate(df, sl, tp, lev, rb, rs, use_adx=adx_f, cooldown=cd, trailing_pct=trail)
            results[(name, label)] = r
            row += f" | {r['pnl']:>7.2f} {r['n']:>3d} {r['wr']*100:>3.0f}% {r['pf']:>4.2f} {r['max_dd']*100:>3.0f}%"
        print(row)

    # Rank by avg PF
    print("\n" + "=" * 140)
    print("RANKED by avg PF:")
    ranked = []
    for name, *_ in configs:
        pfs = [results[(name, l)]["pf"] for l in dfs if results[(name, l)]["n"] > 0]
        pnls = [results[(name, l)]["pnl"] for l in dfs]
        n_pos = sum(1 for p in pnls if p > 0)
        avg_pf = float(np.mean(pfs)) if pfs else 0
        total_pnl = sum(pnls)
        ranked.append((name, total_pnl, avg_pf, n_pos, pnls))
    ranked.sort(key=lambda x: (x[3], x[2]), reverse=True)
    for name, pnl, pf, np_pos, pnls in ranked[:8]:
        marker = "🏆" if pf > 1.3 and np_pos == len(dfs) else "  "
        print(f"  {marker} {name:<35}  sum={pnl:>+8.2f}  avgPF={pf:.2f}  pos={np_pos}/{len(dfs)}  PnLs={[round(p, 2) for p in pnls]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
