#!/usr/bin/env python3
"""3-window validation of trailing-stop configs on 5m."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sweep_timeframe import simulate


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> int:
    base = Path(__file__).resolve().parent.parent / "data" / "cache"
    windows = {
        "A:up":    ("BTCUSDT_5m_90d.csv", "2026-03-29", "2026-05-18"),
        "B:down1": ("BTCUSDT_5m_60d.csv", "2026-04-18", "2026-06-17"),
        "C:down2": ("BTCUSDT_5m_30d_recent.csv", "2026-05-28", "2026-06-27"),
    }

    configs = [
        ("RSI30/70 SL3.0 trail4.0%",     0.030, 0, 5, 30, 70, True,  12, 0.040),
        ("RSI30/70 SL3.0 trail3.0%",     0.030, 0, 5, 30, 70, True,  12, 0.030),
        ("RSI30/70 SL3.0 trail5.0%",     0.030, 0, 5, 30, 70, True,  12, 0.050),
        ("RSI25/75 SL3.0 trail4.0%",     0.030, 0, 5, 25, 75, True,  12, 0.040),
        ("RSI30/70 SL2.5 trail4.0%",     0.025, 0, 5, 30, 70, True,  12, 0.040),
        ("RSI30/70 SL3.0 trail4.0 noADX",0.030, 0, 5, 30, 70, False, 12, 0.040),
        ("RSI30/70 SL3.0 TP6.0 fixed",   0.030, 0.060, 5, 30, 70, True,  12, None),
        ("RSI20/80 SL1.5 TP3.0 (old)",   0.015, 0.030, 5, 20, 80, True,  12, None),
        ("v7 RSI15/85 SL1.5 TP3.0 NOW",  0.015, 0.030, 5, 15, 85, True,  12, None),
    ]

    print("=" * 130)
    hdr = f"{'Config':<35}"
    for w in windows:
        hdr += f" | {w:>12}  PnL   N  WR  PF   DD"
    hdr += f" | {'TOTAL':>6} PnL  pos"
    print(hdr)
    print("-" * 130)

    for name, sl, tp, lev, rb, rs, adx_f, cd, trail in configs:
        row = f"{name:<35}"
        total_pnl = 0.0
        total_n = 0
        all_pnls = []
        all_pfs = []
        for wlabel, (fname, start, end) in windows.items():
            df = load_csv(str(base / fname))
            df = df.loc[start:end]
            r = simulate(df, sl, tp, lev, rb, rs, use_adx=adx_f, cooldown=cd, trailing_pct=trail)
            row += f" | {r['pnl']:>8.2f} {r['n']:>3d} {r['wr']*100:>3.0f}% {r['pf']:>4.2f} {r['max_dd']*100:>3.0f}%"
            total_pnl += r["pnl"]
            total_n += r["n"]
            all_pnls.append(r["pnl"])
            if r["n"] > 0:
                all_pfs.append(r["pf"])
        n_pos = sum(1 for p in all_pnls if p > 0)
        avg_pf = float(np.mean(all_pfs)) if all_pfs else 0
        marker = " ***" if avg_pf > 1.3 and n_pos == 3 else ""
        print(row + f" | {total_pnl:>+7.2f}  {n_pos}/3{marker}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
