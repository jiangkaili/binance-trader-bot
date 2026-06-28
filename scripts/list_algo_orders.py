"""List algo (conditional) open orders with createTime.

Read-only. Uses trader.exchange.BinanceFutures — no duplicated signing.

Usage:
    python scripts/list_algo_orders.py [--symbol BTCUSDT]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trader.config import get_connection_config
from trader.exchange import BinanceFutures


def _log(level: str, msg: str) -> None:
    if level in ("ERROR", "WARN"):
        print(f"[{level}] {msg}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    args = ap.parse_args()

    env_file = os.getenv("ENV_FILE", ".env")
    base_url, proxies, api_key, api_secret = get_connection_config(env_file, market="futures")
    ex = BinanceFutures(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        symbol=args.symbol,
        dry_run=False,
        log=_log,
    )
    ex.sync_time()

    orders = ex.get_open_algo_orders()
    if not orders:
        print(f"(no algo orders on {args.symbol})")
        return 0

    for o in orders:
        ct = datetime.fromtimestamp(o["createTime"] / 1000, tz=timezone.utc)
        print(f"{o['orderType']:<22} side={o['side']:<4} trigger={o['triggerPrice']:<10} "
              f"qty={o['quantity']:<6} reduceOnly={o['reduceOnly']} "
              f"createTime={ct.isoformat()} algoId={o['algoId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
