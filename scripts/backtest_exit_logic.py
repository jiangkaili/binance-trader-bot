"""Specialized backtest replicating LIVE bot exit logic.

Models RSI(7) 15/85 entries on 5m bars with:
  - 5x leverage
  - 0.04% taker fee each side (Binance Futures VIP0)
  - Intra-bar SL/TP triggered by bar high/low (conservative — assumes
    worst-case fill at the trigger price)
  - ADX(14) > 25 trend filter blocks new entries
  - EMA(200) trend-alignment filter: only long above EMA, only short below
  - 12-bar cooldown after any trade close
  - Multiple exit modes for comparison

This is intentionally separate from gridtrader.quant.backtest because
that engine is spot-style (no leverage, no SL/TP) and we can't compare
apples to apples without modeling exchange-side stops.

Usage:
    python scripts/backtest_exit_logic.py --csv data/cache/BTCUSDT_5m_14d.csv
"""
# 专用回测，复现实盘机器人退出逻辑。基于5分钟K线RSI(7) 15/85入场，
# 5倍杠杆，每边0.04%吃单手续费，K线内止损/止盈触发，ADX+EMA200趋势过滤，
# 平仓后12根K线冷却，支持多种退出模式对比。
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant import indicators as ind


# ============================================================================
# Constants matching live config (v7) / 匹配实盘配置(v7)的常量
# ============================================================================
LEVERAGE = 5
TARGET_POSITION_USDT = 15.0       # margin per trade / 每笔交易的保证金
TAKER_FEE_RATE = 0.0004           # 0.04% per side, 0.08% round-trip / 每边0.04%，往返0.08%
INITIAL_EQUITY = 40.0             # ~ current account / 约当前账户
RSI_PERIOD = 7
RSI_OVERSOLD = 15.0
RSI_OVERBOUGHT = 85.0
COOLDOWN_BARS = 12                # bars to wait after a trade before re-entry / 交易后等待的K线数

# Trend filters (matching live) / 趋势过滤（匹配实盘）
ADX_PERIOD = 14
ADX_THRESHOLD = 25.0              # >25 = strong trend, skip entry / >25=强趋势，跳过入场
EMA_TREND_PERIOD = 200


@dataclass
class Trade:
    open_ts: pd.Timestamp
    close_ts: pd.Timestamp
    side: str
    entry: float
    exit: float
    qty: float
    pnl: float  # net of fees / 扣除手续费后的净值
    exit_reason: str
    bars_held: int
    max_favorable_pct: float  # peak profit during trade / 交易期间的最大盈利
    max_adverse_pct: float    # worst drawdown during trade / 交易期间的最大回撤


@dataclass
class ExitPolicy:
    """How positions are exited."""
    sl_pct: float                  # stop-loss % from entry / 距入场价的止损百分比
    tp_pct: Optional[float] = None # take-profit %; None = no TP / 止盈百分比；None = 无止盈
    use_signal_exit: bool = False  # exit on RSI crossback (legacy) / RSI回穿时退出（旧版逻辑）
    trailing_pct: Optional[float] = None  # trailing stop distance, None=off / 追踪止损距离，None=关闭
    breakeven_trigger: Optional[float] = None  # move SL to entry once profit > this % / 盈利超过此百分比时将止损移至入场价
    name: str = "unnamed"

    def describe(self) -> str:
        parts = [f"SL={self.sl_pct*100:.2f}%"]
        if self.tp_pct:
            parts.append(f"TP={self.tp_pct*100:.2f}%")
        if self.use_signal_exit:
            parts.append("signal-exit")
        if self.breakeven_trigger:
            parts.append(f"BE@{self.breakeven_trigger*100:.2f}%")
        if self.trailing_pct:
            parts.append(f"trail={self.trailing_pct*100:.2f}%")
        return " ".join(parts)


def run_backtest(
    df: pd.DataFrame,
    policy: ExitPolicy,
    allow_short: bool = True,
    use_trend_filter: bool = True,
    use_ema_filter: bool = True,
) -> list[Trade]:
    """Replay bars and simulate trades using exchange-style SL/TP fills.

    Entry logic matches RsiRevertStrategy: RSI < oversold → BUY, RSI > overbought → SELL
    (direct threshold, not crossback). Trend filters (ADX + EMA) match live config.
    """
    trades: list[Trade] = []

    # Pre-compute indicators on full series (live does this incrementally) / 在完整序列上预计算指标（实盘为增量计算）
    rsi = ind.rsi(df["close"], RSI_PERIOD)
    adx_s = ind.adx(df, ADX_PERIOD) if use_trend_filter else None
    ema_s = ind.ema(df["close"], EMA_TREND_PERIOD) if use_ema_filter else None

    position = None  # dict if open / 开仓时为字典
    cooldown_until = 0

    for i in range(max(RSI_PERIOD + 1, EMA_TREND_PERIOD + 1), len(df)):
        bar = df.iloc[i]
        ts = df.index[i]
        rsi_now = float(rsi.iloc[i])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])

        # ===== exit handling for open position ===== / ===== 持仓的退出处理 =====
        if position:
            position["bars_held"] += 1
            if position["side"] == "LONG":
                # Track favorable/adverse during bar / 追踪K线内的有利/不利变动
                fav = (bar_high - position["entry"]) / position["entry"]
                adv = (bar_low - position["entry"]) / position["entry"]
                position["max_fav"] = max(position["max_fav"], fav)
                position["max_adv"] = min(position["max_adv"], adv)

                # Update trailing stop / breakeven / 更新追踪止损/保本
                if policy.breakeven_trigger and fav >= policy.breakeven_trigger:
                    if position["effective_sl"] < position["entry"]:
                        position["effective_sl"] = position["entry"]  # lock breakeven / 锁定保本
                if policy.trailing_pct and fav > 0:
                    new_trail = bar_high * (1 - policy.trailing_pct)
                    if new_trail > position["effective_sl"]:
                        position["effective_sl"] = new_trail

                # Check exits (priority: SL first, conservative) / 检查退出（优先级：止损优先，保守策略）
                exit_price = None
                exit_reason = None
                if bar_low <= position["effective_sl"]:
                    exit_price = position["effective_sl"]
                    exit_reason = "SL" if exit_price < position["entry"] else (
                        "BE" if exit_price == position["entry"] else "TRAIL"
                    )
                elif policy.tp_pct and bar_high >= position["entry"] * (1 + policy.tp_pct):
                    exit_price = position["entry"] * (1 + policy.tp_pct)
                    exit_reason = "TP"
                elif policy.use_signal_exit:
                    # RSI crossed back above oversold from below (mean rev complete) / RSI从下方回升超卖线（均值回归完成）
                    if rsi_now > RSI_OVERSOLD:
                        exit_price = bar_close
                        exit_reason = "SIGNAL"

                if exit_price is not None:
                    pnl = _close_trade(position, exit_price, "LONG")
                    trades.append(Trade(
                        open_ts=position["open_ts"], close_ts=ts,
                        side="LONG", entry=position["entry"], exit=exit_price,
                        qty=position["qty"], pnl=pnl, exit_reason=exit_reason,
                        bars_held=position["bars_held"],
                        max_favorable_pct=position["max_fav"],
                        max_adverse_pct=position["max_adv"],
                    ))
                    position = None
                    cooldown_until = i + COOLDOWN_BARS

            else:  # SHORT / 做空
                fav = (position["entry"] - bar_low) / position["entry"]
                adv = (position["entry"] - bar_high) / position["entry"]
                position["max_fav"] = max(position["max_fav"], fav)
                position["max_adv"] = min(position["max_adv"], adv)

                if policy.breakeven_trigger and fav >= policy.breakeven_trigger:
                    if position["effective_sl"] > position["entry"]:
                        position["effective_sl"] = position["entry"]
                if policy.trailing_pct and fav > 0:
                    new_trail = bar_low * (1 + policy.trailing_pct)
                    if new_trail < position["effective_sl"]:
                        position["effective_sl"] = new_trail

                exit_price = None
                exit_reason = None
                if bar_high >= position["effective_sl"]:
                    exit_price = position["effective_sl"]
                    exit_reason = "SL" if exit_price > position["entry"] else (
                        "BE" if exit_price == position["entry"] else "TRAIL"
                    )
                elif policy.tp_pct and bar_low <= position["entry"] * (1 - policy.tp_pct):
                    exit_price = position["entry"] * (1 - policy.tp_pct)
                    exit_reason = "TP"
                elif policy.use_signal_exit:
                    if rsi_now < RSI_OVERBOUGHT:
                        exit_price = bar_close
                        exit_reason = "SIGNAL"

                if exit_price is not None:
                    pnl = _close_trade(position, exit_price, "SHORT")
                    trades.append(Trade(
                        open_ts=position["open_ts"], close_ts=ts,
                        side="SHORT", entry=position["entry"], exit=exit_price,
                        qty=position["qty"], pnl=pnl, exit_reason=exit_reason,
                        bars_held=position["bars_held"],
                        max_favorable_pct=position["max_fav"],
                        max_adverse_pct=position["max_adv"],
                    ))
                    position = None
                    cooldown_until = i + COOLDOWN_BARS

        # ===== entry handling (only if flat & cooldown done) ===== / ===== 入场处理（仅在空仓且冷却完成时）=====
        if position is None and i >= cooldown_until:
            # Trend filter: skip when ADX > threshold (strong trend) / 趋势过滤：ADX>阈值时跳过（强趋势）
            if use_trend_filter and adx_s is not None:
                adx_now = float(adx_s.iloc[i])
                if adx_now > ADX_THRESHOLD:
                    continue  # trending market — mean reversion gets chewed up / 趋势市场——均值回归被绞杀

            # Entry: RSI < oversold → BUY, RSI > overbought → SELL (direct threshold, matches live) / 入场：RSI<超卖→买，RSI>超买→卖（直接阈值，匹配实盘）
            if rsi_now < RSI_OVERSOLD:
                # EMA filter: only long above EMA / EMA过滤：只在EMA上方做多
                if use_ema_filter and ema_s is not None:
                    ema_now = float(ema_s.iloc[i])
                    if bar_close < ema_now:
                        continue  # bearish — skip long / 看跌——跳过做多
                position = _open_long(bar_close, ts, policy)
            elif allow_short and rsi_now > RSI_OVERBOUGHT:
                # EMA filter: only short below EMA / EMA过滤：只在EMA下方做空
                if use_ema_filter and ema_s is not None:
                    ema_now = float(ema_s.iloc[i])
                    if bar_close > ema_now:
                        continue  # bullish — skip short / 看涨——跳过做空
                position = _open_short(bar_close, ts, policy)

    return trades


def _open_long(price: float, ts, policy: ExitPolicy) -> dict:
    notional = TARGET_POSITION_USDT * LEVERAGE
    qty = round(notional / price, 3)
    if qty <= 0:
        qty = 0.001
    return {
        "side": "LONG",
        "entry": price,
        "qty": qty,
        "open_ts": ts,
        "effective_sl": price * (1 - policy.sl_pct),
        "max_fav": 0.0,
        "max_adv": 0.0,
        "bars_held": 0,
    }


def _open_short(price: float, ts, policy: ExitPolicy) -> dict:
    notional = TARGET_POSITION_USDT * LEVERAGE
    qty = round(notional / price, 3)
    if qty <= 0:
        qty = 0.001
    return {
        "side": "SHORT",
        "entry": price,
        "qty": qty,
        "open_ts": ts,
        "effective_sl": price * (1 + policy.sl_pct),
        "max_fav": 0.0,
        "max_adv": 0.0,
        "bars_held": 0,
    }


def _close_trade(pos: dict, exit_price: float, side: str) -> float:
    """Net PnL after taker fees on both sides."""
    qty = pos["qty"]
    entry = pos["entry"]
    if side == "LONG":
        gross = (exit_price - entry) * qty
    else:
        gross = (entry - exit_price) * qty
    fees = (entry * qty * TAKER_FEE_RATE) + (exit_price * qty * TAKER_FEE_RATE)
    return gross - fees


def summarize(trades: list[Trade], label: str, days: float) -> dict:
    if not trades:
        return {"label": label, "n": 0, "pnl": 0.0}
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = sum(pnls)
    win_rate = 100 * len(wins) / len(trades)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    rr = (avg_win / abs(avg_loss)) if avg_loss else float("inf")
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    expectancy = total / len(trades)
    # Reasons breakdown / 退出原因统计
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    pnl_per_day = total / days if days > 0 else 0
    return {
        "label": label,
        "n": len(trades),
        "pnl": total,
        "pnl_per_day": pnl_per_day,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr": rr,
        "pf": pf,
        "expectancy": expectancy,
        "reasons": reasons,
    }


def print_summary(s: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  {s['label']}")
    print(f"{'='*60}")
    if s["n"] == 0:
        print("  no trades")
        return
    pf_s = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    rr_s = "inf" if s["rr"] == float("inf") else f"{s['rr']:.2f}"
    print(f"  trades       : {s['n']}")
    print(f"  total PnL    : {s['pnl']:+.2f} USDT  ({s['pnl_per_day']:+.2f}/day)")
    print(f"  win rate     : {s['win_rate']:.1f}%")
    print(f"  avg win/loss : {s['avg_win']:+.3f}  /  {s['avg_loss']:+.3f}")
    print(f"  reward/risk  : {rr_s}  (need >{(100-s['win_rate'])/max(s['win_rate'],1):.2f} for breakeven at this WR)")
    print(f"  profit factor: {pf_s}")
    print(f"  expectancy   : {s['expectancy']:+.3f} per trade")
    print(f"  exit reasons : {s['reasons']}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--no-trend-filter", action="store_true",
                   help="disable ADX trend filter (for comparison) / 禁用ADX趋势过滤（用于对比）")
    p.add_argument("--no-ema-filter", action="store_true",
                   help="disable EMA trend-alignment filter / 禁用EMA趋势对齐过滤")
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    df = df.rename(columns={"open_time": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)

    days = (df.index[-1] - df.index[0]).total_seconds() / 86400
    print(f"Loaded {len(df)} bars, {days:.1f} days")
    print(f"  {df.index[0]} -> {df.index[-1]}")
    print(f"  close: {df['close'].iloc[0]:.1f} -> {df['close'].iloc[-1]:.1f} "
          f"({(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.2f}%)")
    print(f"  config: lev={LEVERAGE}x target={TARGET_POSITION_USDT}U RSI={RSI_OVERSOLD}/{RSI_OVERBOUGHT} "
          f"cooldown={COOLDOWN_BARS}bars ADX>{ADX_THRESHOLD} EMA{EMA_TREND_PERIOD}")

    use_tf = not args.no_trend_filter
    use_ef = not args.no_ema_filter

    policies = [
        ExitPolicy(
            name="LIVE (current v7): SL=1.5% + TP=3.0%",
            sl_pct=0.015,
            tp_pct=0.030,
        ),
        ExitPolicy(
            name="LIVE v7 no-signal-exit: SL=1.5% + signal-exit (legacy)",
            sl_pct=0.015,
            tp_pct=None,
            use_signal_exit=True,
        ),
        ExitPolicy(
            name="PROP-A: 0.4% SL + 1% TP (raw 2.5:1)",
            sl_pct=0.004,
            tp_pct=0.01,
        ),
        ExitPolicy(
            name="PROP-B: trailing 0.3% + BE@0.3% + 0.4% SL",
            sl_pct=0.004,
            tp_pct=None,
            breakeven_trigger=0.003,
            trailing_pct=0.003,
        ),
        ExitPolicy(
            name="PROP-C: trailing 0.4% + BE@0.5% + 0.5% SL",
            sl_pct=0.005,
            tp_pct=None,
            breakeven_trigger=0.005,
            trailing_pct=0.004,
        ),
        ExitPolicy(
            name="PROP-D: trailing 0.2% + BE@0.3% + 0.3% SL",
            sl_pct=0.003,
            tp_pct=None,
            breakeven_trigger=0.003,
            trailing_pct=0.002,
        ),
        ExitPolicy(
            name="PROP-E: 0.5% SL + 1.5% TP (fixed 3:1)",
            sl_pct=0.005,
            tp_pct=0.015,
        ),
        ExitPolicy(
            name="PROP-F: trailing 0.5% + BE@0.7% + 0.6% SL (loose)",
            sl_pct=0.006,
            tp_pct=None,
            breakeven_trigger=0.007,
            trailing_pct=0.005,
        ),
    ]

    results = []
    for pol in policies:
        trades = run_backtest(df, pol, use_trend_filter=use_tf, use_ema_filter=use_ef)
        s = summarize(trades, f"{pol.name}  [{pol.describe()}]", days)
        results.append((pol, s, trades))
        print_summary(s)

    print(f"\n{'='*60}")
    print("  RANKING by total PnL")
    print(f"{'='*60}")
    results.sort(key=lambda x: x[1].get("pnl", -1e9), reverse=True)
    for pol, s, _ in results:
        if s["n"] == 0:
            continue
        pf_s = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        print(f"  {s['pnl']:+7.2f} USDT  ({s['n']:3d} trades, WR {s['win_rate']:4.1f}%, PF {pf_s}, exp {s['expectancy']:+.3f})  {pol.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
