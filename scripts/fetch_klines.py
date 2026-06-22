"""Fetch historical klines (candlestick) data from Binance public API.

No API key required — these are public market data endpoints.

Usage:
    python scripts/fetch_klines.py --symbol BTCUSDT --interval 1h --days 30
    python scripts/fetch_klines.py --symbol ETHUSDT --interval 15m --days 7
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

HOSTS = {
    "testnet": "https://testnet.binance.vision",
    "prod":    "https://api.binance.com",
}


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    base = HOSTS["prod"]
    url = base + "/api/v3/klines"
    all_rows: list = []
    cur = start_ms
    while cur < end_ms:
        r = requests.get(
            url,
            params={"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000},
            timeout=15,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        all_rows.extend(batch)
        # Binance returns up to 1000 candles; advance past the last timestamp
        cur = batch[-1][0] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.05)  # public rate limit: 1200 req/min -> 50ms is safe
    return all_rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--out", default=None,
                   help="output CSV path (default: data/cache/<symbol>_<interval>_<days>d.csv)")
    args = p.parse_args()

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * 24 * 3600 * 1000

    print(f"Fetching {args.symbol} {args.interval} for last {args.days} days")
    print(f"  from {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).isoformat()}")
    print(f"  to   {datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).isoformat()}")
    rows = fetch_klines(args.symbol, args.interval, start_ms, end_ms)
    print(f"  got  {len(rows)} candles")

    if not rows:
        print("  no data returned — check symbol / interval")
        return 1

    if args.out is None:
        out_path = Path("data/cache") / f"{args.symbol}_{args.interval}_{args.days}d.csv"
    else:
        out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
        for row in rows:
            w.writerow(row)
    print(f"  saved {out_path}")
    print()
    # Show first/last for sanity
    print(f"  first: {datetime.fromtimestamp(rows[0][0]/1000, tz=timezone.utc)}  close={rows[0][4]}")
    print(f"  last : {datetime.fromtimestamp(rows[-1][0]/1000, tz=timezone.utc)}  close={rows[-1][4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
