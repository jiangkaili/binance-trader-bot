"""Multi-strategy, multi-window backtest with SL/TP + leverage simulation.

Tests RSI mean-reversion, MA crossover, Bollinger, and Momentum strategies
across 3 time windows with train/test split to avoid overfitting.

Usage: python scripts/sweep_multi.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

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


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> dict:
    mid = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    return {"upper": mid + num_std * std, "mid": mid, "lower": mid - num_std * std}


# ---------------------------------------------------------------------------
# Strategy configs — each returns a signal: 1=long, -1=short, 0=flat
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    name: str
    # Common
    sl_pct: float
    tp_pct: float
    leverage: int
    cooldown_bars: int = 12
    # Strategy-specific
    rsi_period: int = 7
    rsi_buy: float = 12.0
    rsi_sell: float = 88.0
    adx_threshold: float = 25.0
    use_adx: bool = True
    strategy_type: str = "rsi_revert"  # rsi_revert | ma_cross | bollinger | momentum
    ma_fast: int = 8
    ma_slow: int = 21
    bb_period: int = 20
    bb_std: float = 2.0
    mom_period: int = 20
    mom_threshold: float = 0.02


def compute_signals(df: pd.DataFrame, cfg: StrategyConfig) -> pd.Series:
    """Return a Series of signals: 1=long entry, -1=short entry, 0=flat."""
    signals = pd.Series(0, index=df.index)

    if cfg.strategy_type == "rsi_revert":
        r = rsi(df["close"], cfg.rsi_period)
        a = adx(df["high"], df["low"], df["close"], 14)
        for i in range(50, len(df)):
            if pd.isna(r.iloc[i]) or pd.isna(a.iloc[i]):
                continue
            if cfg.use_adx and a.iloc[i] > cfg.adx_threshold:
                continue
            if r.iloc[i] < cfg.rsi_buy:
                signals.iloc[i] = 1
            elif r.iloc[i] > cfg.rsi_sell:
                signals.iloc[i] = -1

    elif cfg.strategy_type == "rsi_revert_ema":
        r = rsi(df["close"], cfg.rsi_period)
        a = adx(df["high"], df["low"], df["close"], 14)
        ema200 = ema(df["close"], 200)
        for i in range(200, len(df)):
            if pd.isna(r.iloc[i]) or pd.isna(a.iloc[i]) or pd.isna(ema200.iloc[i]):
                continue
            if cfg.use_adx and a.iloc[i] > cfg.adx_threshold:
                continue
            close = float(df["close"].iloc[i])
            above_ema = close > float(ema200.iloc[i])
            # Long only when price above EMA (uptrend), short only when below (downtrend)
            if r.iloc[i] < cfg.rsi_buy and above_ema:
                signals.iloc[i] = 1
            elif r.iloc[i] > cfg.rsi_sell and not above_ema:
                signals.iloc[i] = -1

    elif cfg.strategy_type == "ma_cross":
        fast = ema(df["close"], cfg.ma_fast)
        slow = ema(df["close"], cfg.ma_slow)
        for i in range(50, len(df)):
            if pd.isna(fast.iloc[i]) or pd.isna(slow.iloc[i]):
                continue
            if i > 0 and fast.iloc[i - 1] <= slow.iloc[i - 1] and fast.iloc[i] > slow.iloc[i]:
                signals.iloc[i] = 1  # golden cross
            elif i > 0 and fast.iloc[i - 1] >= slow.iloc[i - 1] and fast.iloc[i] < slow.iloc[i]:
                signals.iloc[i] = -1  # death cross

    elif cfg.strategy_type == "bollinger":
        bb = bollinger(df["close"], cfg.bb_period, cfg.bb_std)
        for i in range(50, len(df)):
            if pd.isna(bb["upper"].iloc[i]):
                continue
            if df["close"].iloc[i] < bb["lower"].iloc[i]:
                signals.iloc[i] = 1
            elif df["close"].iloc[i] > bb["upper"].iloc[i]:
                signals.iloc[i] = -1

    elif cfg.strategy_type == "momentum":
        mom = df["close"].pct_change(cfg.mom_period)
        for i in range(50, len(df)):
            if pd.isna(mom.iloc[i]):
                continue
            if mom.iloc[i] > cfg.mom_threshold:
                signals.iloc[i] = 1
            elif mom.iloc[i] < -cfg.mom_threshold:
                signals.iloc[i] = -1

    return signals


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

def simulate(df: pd.DataFrame, cfg: StrategyConfig, margin_usdt: float = 16.0,
             fee_bps: float = 6.0) -> dict:
    fee_rate = fee_bps / 10000
    signals = compute_signals(df, cfg)

    cash = margin_usdt
    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    qty = 0.0
    last_trade_bar = -999
    trades = []

    for i in range(50, len(df)):
        bar = df.iloc[i]
        high = float(bar["high"])
        low = float(bar["low"])

        # Check SL/TP
        if position != 0:
            close_fee = qty * fee_rate
            if position == 1:
                sl_price = entry_price * (1 - cfg.sl_pct)
                tp_price = entry_price * (1 + cfg.tp_pct)
                if low <= sl_price:
                    pnl = (sl_price - entry_price) * qty - sl_price * close_fee
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "long", "reason": "SL"})
                    position = 0
                    last_trade_bar = i
                elif high >= tp_price:
                    pnl = (tp_price - entry_price) * qty - tp_price * close_fee
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "long", "reason": "TP"})
                    position = 0
                    last_trade_bar = i
            elif position == -1:
                sl_price = entry_price * (1 + cfg.sl_pct)
                tp_price = entry_price * (1 - cfg.tp_pct)
                if high >= sl_price:
                    pnl = (entry_price - sl_price) * qty - sl_price * close_fee
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "short", "reason": "SL"})
                    position = 0
                    last_trade_bar = i
                elif low <= tp_price:
                    pnl = (entry_price - tp_price) * qty - tp_price * close_fee
                    cash += pnl
                    trades.append({"pnl": pnl, "side": "short", "reason": "TP"})
                    position = 0
                    last_trade_bar = i

        # Check for new entry
        if position == 0 and (i - last_trade_bar) >= cfg.cooldown_bars:
            sig = int(signals.iloc[i])
            if sig == 0:
                continue
            notional = cash * cfg.leverage
            if notional <= 0:
                break
            entry_price = float(bar["close"])
            qty = notional / entry_price
            entry_fee = entry_price * qty * fee_rate
            cash -= entry_fee
            position = sig  # 1 or -1

    # Close remaining
    if position != 0:
        last_close = float(df.iloc[-1]["close"])
        close_fee = qty * fee_rate
        if position == 1:
            pnl = (last_close - entry_price) * qty - last_close * close_fee
        else:
            pnl = (entry_price - last_close) * qty - last_close * close_fee
        cash += pnl
        trades.append({"pnl": pnl, "side": "long" if position == 1 else "short", "reason": "EOD"})

    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    n = len(trades)
    wr = len(wins) / n if n > 0 else 0
    avg_win = float(np.mean([t["pnl"] for t in wins])) if wins else 0
    avg_loss = float(np.mean([t["pnl"] for t in losses])) if losses else 0
    tp_n = len([t for t in trades if t["reason"] == "TP"])
    sl_n = len([t for t in trades if t["reason"] == "SL"])
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    expectancy = wr * avg_win + (1 - wr) * avg_loss if n > 0 else 0

    return {
        "total_pnl": total_pnl,
        "final_cash": cash,
        "n_trades": n,
        "win_rate": wr,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "tp_count": tp_n,
        "sl_count": sl_n,
        "rr": rr,
        "expectancy": expectancy,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def main() -> int:
    base = Path(__file__).resolve().parent.parent / "data" / "cache"

    # Three windows: train (early), test1 (mid), test2 (recent/live period)
    windows = {
        "A:train (Mar29-May18)":  ("BTCUSDT_5m_90d.csv", "2026-03-29", "2026-05-18"),
        "B:test1 (Apr18-Jun17)":  ("BTCUSDT_5m_60d.csv", "2026-04-18", "2026-06-17"),
        "C:test2 (May28-Jun27)":  ("BTCUSDT_5m_30d_recent.csv", "2026-05-28", "2026-06-27"),
    }

    dfs = {}
    for label, (fname, start, end) in windows.items():
        path = base / fname
        if not path.exists():
            print(f"SKIP {label}: {fname} not found")
            continue
        df = load_csv(str(path))
        df = df.loc[start:end]
        dfs[label] = df
        change = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        print(f"{label}: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}, "
              f"price {df['close'].iloc[0]:.0f} -> {df['close'].iloc[-1]:.0f} ({change:+.1f}%)")

    # Strategy configs to test — use keyword args to avoid field-order bugs
    configs = [
        # RSI mean reversion variants (rsi_period=7, ADX<25 filter)
        StrategyConfig(name="RSI 12/88 SL0.5 TP1.0 10x", sl_pct=0.005, tp_pct=0.010, leverage=10,
                       rsi_buy=12, rsi_sell=88, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 12/88 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=12, rsi_sell=88, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 12/88 SL1.5 TP3.0 5x noADX", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=12, rsi_sell=88, use_adx=False, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 15/85 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=15, rsi_sell=85, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 10/90 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=10, rsi_sell=90, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 12/88 SL2.0 TP4.0 5x", sl_pct=0.020, tp_pct=0.040, leverage=5,
                       rsi_buy=12, rsi_sell=88, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 12/88 SL1.0 TP2.0 5x", sl_pct=0.010, tp_pct=0.020, leverage=5,
                       rsi_buy=12, rsi_sell=88, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),
        StrategyConfig(name="RSI 20/80 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=20, rsi_sell=80, use_adx=True, adx_threshold=25, strategy_type="rsi_revert"),

        # v7: RSI 10/90 with EMA200 trend-alignment (long only above EMA, short only below)
        StrategyConfig(name="RSI 10/90 SL1.5 TP3.0 5x +EMA200", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=10, rsi_sell=90, use_adx=True, adx_threshold=25, strategy_type="rsi_revert_ema"),
        StrategyConfig(name="RSI 12/88 SL1.5 TP3.0 5x +EMA200", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=12, rsi_sell=88, use_adx=True, adx_threshold=25, strategy_type="rsi_revert_ema"),
        StrategyConfig(name="RSI 15/85 SL1.5 TP3.0 5x +EMA200", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=15, rsi_sell=85, use_adx=True, adx_threshold=25, strategy_type="rsi_revert_ema"),
        StrategyConfig(name="RSI 10/90 SL2.0 TP4.0 5x +EMA200", sl_pct=0.020, tp_pct=0.040, leverage=5,
                       rsi_buy=10, rsi_sell=90, use_adx=True, adx_threshold=25, strategy_type="rsi_revert_ema"),
        StrategyConfig(name="RSI 10/90 SL1.5 TP3.0 5x +EMA200 noADX", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       rsi_buy=10, rsi_sell=90, use_adx=False, adx_threshold=25, strategy_type="rsi_revert_ema"),

        # MA crossover (trend following)
        StrategyConfig(name="MA 8/21 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       strategy_type="ma_cross", ma_fast=8, ma_slow=21),
        StrategyConfig(name="MA 12/26 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       strategy_type="ma_cross", ma_fast=12, ma_slow=26),
        StrategyConfig(name="MA 5/20 SL2.0 TP4.0 5x", sl_pct=0.020, tp_pct=0.040, leverage=5,
                       strategy_type="ma_cross", ma_fast=5, ma_slow=20),
        StrategyConfig(name="MA 8/21 SL1.0 TP3.0 5x", sl_pct=0.010, tp_pct=0.030, leverage=5,
                       strategy_type="ma_cross", ma_fast=8, ma_slow=21),

        # Bollinger (mean reversion, different mechanism)
        StrategyConfig(name="BB 20/2 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       strategy_type="bollinger", bb_period=20, bb_std=2.0),
        StrategyConfig(name="BB 20/2.5 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       strategy_type="bollinger", bb_period=20, bb_std=2.5),
        StrategyConfig(name="BB 20/2 SL2.0 TP4.0 5x", sl_pct=0.020, tp_pct=0.040, leverage=5,
                       strategy_type="bollinger", bb_period=20, bb_std=2.0),
        StrategyConfig(name="BB 20/2.5 SL2.0 TP4.0 5x", sl_pct=0.020, tp_pct=0.040, leverage=5,
                       strategy_type="bollinger", bb_period=20, bb_std=2.5),

        # Momentum (trend following)
        StrategyConfig(name="Mom 20 SL1.5 TP3.0 5x", sl_pct=0.015, tp_pct=0.030, leverage=5,
                       strategy_type="momentum", mom_period=20, mom_threshold=0.01),
        StrategyConfig(name="Mom 20 SL2.0 TP4.0 5x", sl_pct=0.020, tp_pct=0.040, leverage=5,
                       strategy_type="momentum", mom_period=20, mom_threshold=0.01),
    ]

    # Run all configs on all windows
    print("\n" + "=" * 140)
    hdr = f"{'Strategy':<32}"
    for wlabel in dfs:
        short = wlabel.split("(")[0].strip()
        hdr += f" | {short:>16} PnL    N  WR%"
    print(hdr)
    print("-" * 140)

    all_results = {}
    for cfg in configs:
        row = f"{cfg.name:<32}"
        for wlabel, df in dfs.items():
            r = simulate(df, cfg, margin_usdt=16.0)
            key = (cfg.name, wlabel)
            all_results[key] = r
            short = wlabel.split("(")[0].strip()
            row += f" | {r['total_pnl']:>8.2f} {r['n_trades']:>4d} {r['win_rate']*100:>4.0f}%"
        print(row)

    # Summary: which strategies are positive across ALL windows?
    print("\n" + "=" * 140)
    print("CONSISTENCY CHECK: strategies positive across ALL windows")
    print("-" * 140)
    consistent = []
    for cfg in configs:
        pnls = []
        for wlabel in dfs:
            r = all_results[(cfg.name, wlabel)]
            pnls.append(r["total_pnl"])
        all_positive = all(p > 0 for p in pnls)
        min_pnl = min(pnls)
        avg_pnl = np.mean(pnls)
        marker = "✅" if all_positive else "❌"
        print(f"  {marker} {cfg.name:<32}  PnLs: {pnls[0]:>+8.2f} / {pnls[1]:>+8.2f} / {pnls[2]:>+8.2f}"
              f"  avg={avg_pnl:>+7.2f}  min={min_pnl:>+8.2f}")
        if all_positive:
            consistent.append((cfg, avg_pnl, min_pnl, pnls))

    if consistent:
        print(f"\n--- {len(consistent)} consistent strategy(ies) ---")
        for cfg, avg, mn, pnls in sorted(consistent, key=lambda x: x[1], reverse=True):
            print(f"  🏆 {cfg.name}  avg={avg:+.2f}  min={mn:+.2f}")
    else:
        print("\n--- NO strategy is positive across all windows ---")
        # Find least-bad
        best = None
        for cfg in configs:
            pnls = [all_results[(cfg.name, w)]["total_pnl"] for w in dfs]
            avg = np.mean(pnls)
            mn = min(pnls)
            if best is None or avg > best[1]:
                best = (cfg, avg, mn, pnls)
        if best:
            print(f"  Least-bad: {best[0].name}  avg={best[1]:+.2f}  min={best[2]:+.2f}  pnls={best[3]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
