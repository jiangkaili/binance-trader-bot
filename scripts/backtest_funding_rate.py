#!/usr/bin/env python3
"""Backtest: RSI + funding rate confluence strategy (v9).

Compares v8 (RSI-only) vs v9 (RSI + funding rate filter + standalone signal).

Since WSL cannot reach Binance, this script fetches data via the Windows
proxy or a local CSV cache. Run on Windows for live data.

Usage:
    python scripts/backtest_funding_rate.py                    # fetch from API
    python scripts/backtest_funding_rate.py --csv data/funding.csv  # use CSV

Output: 3-window comparison table (v8 baseline vs v9 confluence vs v9 standalone).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.indicators import adx, ema, rsi, funding_zscore


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 1000) -> pd.DataFrame:
    """Fetch klines from Binance API (requires network)."""
    import requests
    base = "https://fapi.binance.com"
    r = requests.get(f"{base}/fapi/v1/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=15)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_funding_rates(symbol: str = "BTCUSDT", limit: int = 200) -> pd.DataFrame:
    """Fetch funding rate history from Binance API."""
    import requests
    base = "https://fapi.binance.com"
    r = requests.get(f"{base}/fapi/v1/fundingRate",
                     params={"symbol": symbol, "limit": limit},
                     timeout=15)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df.set_index("fundingTime")
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df[["fundingRate"]]


def load_csv(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load klines + funding rates from a single CSV with columns:
    timestamp, open, high, low, close, volume, fundingRate"""
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    klines = df[["open", "high", "low", "close", "volume"]].astype(float)
    funding = df[["fundingRate"]].dropna()
    return klines, funding


def merge_funding_into_klines(klines: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill funding rates onto kline timestamps.

    Funding settles every 8h. Between settlements, the current funding rate
    is the most recent one. We forward-fill to align with 5m klines.
    / 将资金费率前向填充到K线时间戳。资金费率每8h结算，结算间用最近值。
    """
    merged = klines.join(funding, how="left")
    merged["fundingRate"] = merged["fundingRate"].ffill().fillna(0.0)
    return merged


def simulate(
    df: pd.DataFrame,
    rsi_period: int = 7,
    rsi_oversold: float = 20.0,
    rsi_overbought: float = 80.0,
    sl_pct: float = 0.015,
    tp_pct: float = 0.030,
    leverage: int = 5,
    margin_usdt: float = 15.0,
    fee_rate: float = 0.0004,
    cooldown_bars: int = 12,
    use_funding: bool = False,
    funding_period: int = 30,
    funding_threshold: float = 2.0,
    funding_extreme: float = 3.0,
    warmup: int = 210,
) -> dict:
    """Backtest a single config. Returns metrics dict.

    If use_funding=True, applies v9 logic:
    - Confluence: RSI signal rejected if funding z-score disagrees
    - Standalone: extreme funding z-score generates signal without RSI
    """
    df = df.copy()
    df["rsi"] = rsi(df["close"], rsi_period)
    df["ema200"] = ema(df["close"], 200)
    df["adx"] = adx(df[["high", "low", "close"]], 14)

    if use_funding and "fundingRate" in df.columns:
        df["fund_z"] = funding_zscore(df["fundingRate"], funding_period)
    else:
        df["fund_z"] = 0.0

    position = None  # dict with side, entry, qty
    equity = margin_usdt
    trades = []
    cooldown_remaining = 0
    losses_streak = 0

    for i in range(warmup, len(df)):
        bar = df.iloc[i]
        close = float(bar["close"])
        rsi_val = float(bar["rsi"])
        adx_val = float(bar["adx"])
        ema_val = float(bar["ema200"])
        z_val = float(bar["fund_z"]) if not np.isnan(bar["fund_z"]) else 0.0

        # Check SL/TP on existing position / 检查持仓止损止盈
        if position:
            pct = (close - position["entry"]) / position["entry"]
            if position["side"] == "SHORT":
                pct = -pct
            if pct <= -sl_pct:
                pnl = position["qty"] * (pct) * close - position["qty"] * close * fee_rate * 2
                equity += pnl
                trades.append({"pnl": pnl, "side": position["side"], "win": pnl > 0})
                if pnl < 0:
                    losses_streak += 1
                else:
                    losses_streak = 0
                position = None
                cooldown_remaining = cooldown_bars
                continue
            elif pct >= tp_pct:
                pnl = position["qty"] * (pct) * close - position["qty"] * close * fee_rate * 2
                equity += pnl
                trades.append({"pnl": pnl, "side": position["side"], "win": pnl > 0})
                if pnl < 0:
                    losses_streak += 1
                else:
                    losses_streak = 0
                position = None
                cooldown_remaining = cooldown_bars
                continue

        # Cooldown / 冷却
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            continue

        # Loss streak cooldown (3 losses → skip) / 连败冷却
        if losses_streak >= 3:
            continue

        if position is not None:
            continue

        # Generate signal / 生成信号
        signal = "FLAT"

        # RSI signal / RSI信号
        if rsi_val < rsi_oversold and close > ema_val and adx_val < 25:
            signal = "BUY"
        elif rsi_val > rsi_overbought and close < ema_val and adx_val < 25:
            signal = "SELL"

        # Funding rate logic (v9) / 资金费率逻辑
        if use_funding:
            # Standalone: extreme funding / 独立信号：极端费率
            if z_val > funding_extreme:
                signal = "SELL"
            elif z_val < -funding_extreme:
                signal = "BUY"
            # Confluence: reject if funding disagrees / 共振：费率不一致则拒绝
            elif signal == "BUY" and z_val > 0:
                signal = "FLAT"
            elif signal == "SELL" and z_val < 0:
                signal = "FLAT"

        if signal == "FLAT":
            continue

        # Open position / 开仓
        qty = (margin_usdt * leverage) / close
        position = {"side": "LONG" if signal == "BUY" else "SHORT",
                     "entry": close, "qty": qty}

    # Close any remaining position / 平掉残余持仓
    if position:
        close = float(df.iloc[-1]["close"])
        pct = (close - position["entry"]) / position["entry"]
        if position["side"] == "SHORT":
            pct = -pct
        pnl = position["qty"] * pct * close - position["qty"] * close * fee_rate * 2
        equity += pnl
        trades.append({"pnl": pnl, "side": position["side"], "win": pnl > 0})

    # Compute metrics / 计算指标
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    total_win = sum(t["pnl"] for t in wins)
    total_loss = abs(sum(t["pnl"] for t in losses))
    pf = total_win / total_loss if total_loss > 0 else float("inf")
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    total_pnl = sum(t["pnl"] for t in trades)

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": pf,
        "total_pnl": total_pnl,
        "final_equity": equity,
        "avg_pnl": total_pnl / len(trades) if trades else 0,
    }


def three_window_backtest(df: pd.DataFrame, label: str, **kwargs) -> list[dict]:
    """Run 3-window backtest: split data into thirds, test each independently."""
    n = len(df)
    third = n // 3
    results = []
    for i, (start, end, name) in enumerate([
        (0, third, "A(前1/3)"),
        (third, 2 * third, "B(中1/3)"),
        (2 * third, n, "C(后1/3)"),
    ]):
        r = simulate(df.iloc[start:end], **kwargs)
        r["window"] = name
        r["label"] = label
        results.append(r)
    return results


def print_results(results: list[dict]) -> None:
    """Print results table."""
    print(f"\n{'Label':<30} {'Window':<12} {'Trades':>7} {'WR%':>6} {'PF':>6} {'PnL':>10}")
    print("-" * 75)
    for r in results:
        print(f"{r['label']:<30} {r['window']:<12} {r['trades']:>7} "
              f"{r['win_rate']:>6.1f} {r['profit_factor']:>6.2f} "
              f"{r['total_pnl']:>+10.4f}")

    # Summary / 汇总
    labels = sorted(set(r["label"] for r in results))
    for label in labels:
        subset = [r for r in results if r["label"] == label]
        avg_pf = sum(r["profit_factor"] for r in subset) / len(subset)
        total_pnl = sum(r["total_pnl"] for r in subset)
        all_positive = all(r["total_pnl"] > 0 for r in subset)
        marker = " ✅" if all_positive else " ❌"
        print(f"  {label}: avgPF={avg_pf:.2f} totalPnL={total_pnl:+.4f} "
              f"3/3 positive={'YES' if all_positive else 'NO'}{marker}")


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest RSI + funding rate strategy (v9)")
    p.add_argument("--csv", help="CSV file with klines+funding data")
    p.add_argument("--limit", type=int, default=1000, help="kline limit (default 1000)")
    args = p.parse_args()

    if args.csv:
        klines, funding = load_csv(args.csv)
        df = merge_funding_into_klines(klines, funding)
        print(f"Loaded {len(df)} bars from CSV")
    else:
        print("Fetching klines + funding rates from Binance...")
        try:
            klines = fetch_klines(limit=args.limit)
            funding = fetch_funding_rates(limit=200)
            df = merge_funding_into_klines(klines, funding)
            print(f"Got {len(klines)} klines, {len(funding)} funding rates")
        except Exception as e:
            print(f"API fetch failed: {e}")
            print("Use --csv to load from file, or run on Windows with proxy.")
            return 1

    print(f"\nData range: {df.index[0]} to {df.index[-1]}")
    print(f"Bars: {len(df)}")

    # v8 baseline: RSI only / v8基线：仅RSI
    v8 = three_window_backtest(
        df, "v8 RSI-only",
        rsi_oversold=20.0, rsi_overbought=80.0,
        sl_pct=0.015, tp_pct=0.030, leverage=5, margin_usdt=15.0,
        use_funding=False,
    )

    # v9: RSI + funding confluence / v9：RSI+资金费率共振
    v9_conf = three_window_backtest(
        df, "v9 RSI+funding(confluence)",
        rsi_oversold=20.0, rsi_overbought=80.0,
        sl_pct=0.015, tp_pct=0.030, leverage=5, margin_usdt=15.0,
        use_funding=True, funding_period=30,
        funding_threshold=2.0, funding_extreme=3.0,
    )

    # v9: funding standalone only / v9：仅资金费率独立信号
    v9_standalone = three_window_backtest(
        df, "v9 funding(standalone)",
        rsi_oversold=0.0, rsi_overbought=100.0,  # disable RSI / 禁用RSI
        sl_pct=0.015, tp_pct=0.030, leverage=5, margin_usdt=15.0,
        use_funding=True, funding_period=30,
        funding_threshold=2.0, funding_extreme=2.0,  # lower threshold for standalone-only / 独立模式降低阈值
    )

    all_results = v8 + v9_conf + v9_standalone
    print_results(all_results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
