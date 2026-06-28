#!/usr/bin/env python3
"""Smoke test: fetch klines → indicators → strategy signal → backtest → storage."""
import sys, os, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from gridtrader.quant.indicators import rsi, ema, adx
from gridtrader.quant.strategies import RsiRevertStrategy
from gridtrader.quant.storage import Store
from gridtrader.quant.backtest import Backtester
from trader.config import TraderConfig

# 1. Fetch klines from testnet (public, no auth)
print("=== 1. Fetch klines (testnet) ===")
r = requests.get("https://testnet.binancefuture.com/fapi/v1/klines",
                 params={"symbol": "BTCUSDT", "interval": "5m", "limit": 300}, timeout=15)
if r.status_code != 200:
    print(f"FAIL: klines HTTP {r.status_code}: {r.text[:200]}")
    sys.exit(1)
print(f"  HTTP {r.status_code}, {len(r.json())} klines")
data = r.json()
cols = ["open_time","open","high","low","close","volume","close_time","quote_vol","trades","taker_buy_vol","taker_buy_quote","ignore"]
df = pd.DataFrame(data, columns=cols)
for c in ["open","high","low","close","volume"]:
    df[c] = df[c].astype(float)
df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
df = df.set_index("open_time")
print(f"  Range: {df.index[0]} -> {df.index[-1]}")
print(f"  Last close: {df['close'].iloc[-1]:.2f}")

# 2. Indicators
print("\n=== 2. Indicators ===")
cfg = TraderConfig.from_yaml()
close = df["close"]
rsi_vals = rsi(close, cfg.rsi_period)
ema200 = ema(close, 200)
adx_vals = adx(df[["high","low","close"]], 14)
print(f"  RSI({cfg.rsi_period}) last 5: {rsi_vals.dropna().tail(5).round(1).tolist()}")
print(f"  EMA(200) last: {ema200.dropna().tail(3).round(2).tolist()}")
print(f"  ADX(14) last 5: {adx_vals.dropna().tail(5).round(1).tolist()}")

# 3. Strategy signal
print("\n=== 3. Strategy signal ===")
strategy = RsiRevertStrategy(period=cfg.rsi_period, oversold=cfg.rsi_oversold, overbought=cfg.rsi_overbought)
sig = strategy.next_signal(df.iloc[-1], df)
print(f"  Signal: side={sig.side}  reason={sig.reason}")
print(f"  RSI={rsi_vals.iloc[-1]:.1f}  ADX={adx_vals.iloc[-1]:.1f}  EMA200={ema200.iloc[-1]:.2f}")

# 4. SQLite storage
print("\n=== 4. SQLite storage ===")
db_path = os.path.join(os.path.dirname(__file__), "..", "data", "trades.db")
store = Store(db_path)
store.log_event(level="INFO", msg="smoke test event", strategy="test")
trades = store.trades()
print(f"  log_event OK, trades in DB: {len(trades)}")

# 5. Backtest
print("\n=== 5. Backtest ===")
bt = Backtester(
    strategy=strategy,
    initial_cash=100.0,
    commission_bps=4.0,
    slippage_bps=2.0,
    position_size_pct=0.15,
    allow_short=True,
)
result = bt.run(df, symbol="BTCUSDT")
print(f"  Trades: {len(result.trades)}")
print(f"  Initial: {result.initial_cash:.2f}  Final: {result.final_equity:.2f}")
m = result.metrics
print(f"  Return: {m.get('total_return', 0)*100:.2f}%")
print(f"  Win rate: {m.get('win_rate', 0)*100:.1f}%")
print(f"  Max DD: {m.get('max_drawdown', 0)*100:.2f}%")

print("\n=== ALL SMOKE TESTS PASSED ===")
