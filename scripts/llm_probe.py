#!/usr/bin/env python3
"""Probe the v10 LLM layer without starting the trading loop.

Fetches live BTCUSDT 5m bars, computes the same indicators `live_trader.py`
would, builds the same prompt, and prints the LLM's assessment. Use it to
verify credentials, latency, and that the model returns parseable JSON —
before letting a real position depend on it.

Usage
-----
    python scripts/llm_probe.py
    python scripts/llm_probe.py --repeat 3      # three calls, see consistency
    python scripts/llm_probe.py --raw           # also dump the raw response

Nothing is ordered. This script only reads market data and calls the LLM.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from gridtrader.quant.indicators import adx as calc_adx, ema as calc_ema, rsi as calc_rsi
from gridtrader.quant.llm_signal import LLMAnalyzer
from trader.config import HOSTS, TraderConfig, load_env_file
from trader.paths import PROJECT_ROOT


def fetch_bars(base: str, symbol: str, interval: str, limit: int):
    import pandas as pd

    r = requests.get(base + "/fapi/v1/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=15)
    r.raise_for_status()
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(r.json(), columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_funding_z(base: str, symbol: str, period: int) -> float:
    from gridtrader.quant.indicators import funding_zscore

    r = requests.get(base + "/fapi/v1/fundingRate",
                     params={"symbol": symbol, "limit": period + 10}, timeout=15)
    r.raise_for_status()
    rows = r.json()
    if len(rows) < period:
        return 0.0
    series = [float(x["fundingRate"]) for x in rows]
    try:
        import pandas as pd
        z = funding_zscore(pd.Series(series), period)
        v = float(z.iloc[-1])
        return 0.0 if v != v else v
    except Exception:
        return 0.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", default=".env")
    p.add_argument("--repeat", type=int, default=1, help="number of calls (consistency check)")
    p.add_argument("--raw", action="store_true", help="dump raw HTTP response")
    args = p.parse_args()

    load_env_file(args.env_file)
    cfg = TraderConfig.from_yaml()

    api_url = os.getenv("LLM_API_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()

    print("=" * 66)
    print("LLM LAYER PROBE (v10)")
    print("=" * 66)
    print(f"  api_url      : {api_url or '(empty)'}")
    print(f"  model        : {model or '(empty)'}")
    print(f"  api_key      : {'set (…' + api_key[-4:] + ')' if api_key else '(EMPTY — LLM will be disabled)'}")
    print(f"  config       : llm_enabled={cfg.llm_enabled} interval={cfg.llm_interval_ticks}ticks "
          f"override_adx={cfg.llm_can_override_adx} min_conf={cfg.llm_min_confidence} "
          f"no_proxy={cfg.llm_no_proxy}")
    print(f"  effective    : every {cfg.llm_interval_ticks} ticks × {cfg.poll_seconds}s "
          f"= {cfg.llm_interval_ticks * cfg.poll_seconds // 60} min")
    print()

    if not (api_url and api_key and model):
        print("LLM not configured — fill LLM_API_URL / LLM_API_KEY / LLM_MODEL in .env")
        return 2

    use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
    base = HOSTS["testnet" if use_testnet else "prod"]
    symbol = cfg.symbol
    print(f"market data from: {base}  symbol={symbol}")

    df = fetch_bars(base, symbol, cfg.kline_interval, 300)
    price = float(df["close"].iloc[-1])
    rsi = float(calc_rsi(df["close"], cfg.rsi_period).iloc[-1])
    adx = float(calc_adx(df, 14).iloc[-1])
    ema200 = float(calc_ema(df["close"], cfg.trend_ema_period).iloc[-1])
    fz = fetch_funding_z(base, symbol, cfg.funding_zscore_period) if cfg.funding_rate_enabled else 0.0

    print(f"price={price:,.2f}  RSI({cfg.rsi_period})={rsi:.1f}  ADX={adx:.1f}  "
          f"EMA200={ema200:,.2f}  funding_z={fz:+.2f}")
    print()

    analyzer = LLMAnalyzer(
        api_url=api_url, api_key=api_key, model=model,
        interval_ticks=cfg.llm_interval_ticks,
        can_override_adx=cfg.llm_can_override_adx,
        min_confidence=cfg.llm_min_confidence,
        no_proxy=cfg.llm_no_proxy,
    )

    for i in range(args.repeat):
        t0 = time.time()
        a = analyzer.assess(
            price=price, rsi=rsi, adx=adx, ema200=ema200, funding_z=fz,
            rsi_signal="FLAT", adx_threshold=cfg.trend_filter_adx_threshold,
            recent_bars=df.tail(12),
        )
        dt = (time.time() - t0) * 1000
        print(f"--- call {i + 1}/{args.repeat} ({dt:.0f} ms) ---")
        if not a.is_valid:
            print(f"  ERROR: {a.error}")
            continue
        print(f"  signal     : {a.signal.value}")
        print(f"  confidence : {a.confidence:.2f}")
        print(f"  regime     : {a.regime}")
        print(f"  reasoning  : {a.reasoning}")
        gate = []
        if a.confidence < cfg.llm_min_confidence:
            gate.append(f"confidence {a.confidence:.2f} < {cfg.llm_min_confidence} → robot would IGNORE it")
        if a.signal.value == "FLAT":
            gate.append("FLAT → no action")
        if adx > cfg.trend_filter_adx_threshold and a.regime == "exhausting" and cfg.llm_can_override_adx:
            gate.append("regime=exhausting → would OVERRIDE the ADX block")
        print(f"  would do   : {'; '.join(gate) if gate else 'passes the gate, feeds into the signal layer'}")
        print()

    print("stats:", json.dumps(analyzer.stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
