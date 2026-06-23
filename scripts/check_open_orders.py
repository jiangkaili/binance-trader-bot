"""List positions + open orders (regular + algo) + balances.

Read-only. Uses trader.exchange.BinanceFutures — no duplicated signing.

Usage:
    python scripts/check_open_orders.py [--symbol BTCUSDT]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trader.config import load_env_file
from trader.exchange import BinanceFutures


def _log(level: str, msg: str) -> None:
    if level in ("ERROR", "WARN"):
        print(f"[{level}] {msg}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    args = ap.parse_args()

    load_env_file(os.getenv("ENV_FILE", ".env.testnet"))
    ex = BinanceFutures(
        api_key=os.environ["BINANCE_API_KEY"],
        api_secret=os.environ["BINANCE_API_SECRET"],
        base_url=os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com"),
        symbol=args.symbol,
        dry_run=False,
        log=_log,
    )
    ex.sync_time()

    print("=" * 60)
    print("BALANCES")
    print("=" * 60)
    for asset, b in ex.get_account_balance().items():
        if b["balance"] > 0 or b["available"] > 0:
            print(f"  {asset:<6} balance={b['balance']:<14.4f} available={b['available']:.4f}")

    print()
    print("=" * 60)
    print(f"POSITION ({args.symbol})")
    print("=" * 60)
    pos = ex.get_position()
    if pos is None:
        print("  (flat)")
    else:
        print(f"  {pos.side} qty={pos.qty} entry={pos.entry} mark={pos.mark} "
              f"uPnL={pos.u_pnl:+.4f} lev={pos.leverage}x")

    print()
    print("=" * 60)
    print(f"REGULAR OPEN ORDERS ({args.symbol})  [/fapi/v1/openOrders]")
    print("=" * 60)
    regular = ex.get_open_orders()
    if not regular:
        print("  (none)")
    for o in regular:
        ct = datetime.fromtimestamp(o["time"] / 1000, tz=timezone.utc)
        print(f"  {o['type']:<14} {o['side']:<4} qty={o['origQty']} price={o['price']} "
              f"reduceOnly={o.get('reduceOnly')} orderId={o['orderId']} {ct.isoformat()}")

    print()
    print("=" * 60)
    print(f"ALGO OPEN ORDERS ({args.symbol})  [/fapi/v1/openAlgoOrders]")
    print("=" * 60)
    algo = ex.get_open_algo_orders()
    if not algo:
        print("  (none — naked position if any!)")
    for o in algo:
        ct = datetime.fromtimestamp(o["createTime"] / 1000, tz=timezone.utc)
        print(f"  {o['orderType']:<22} {o['side']:<4} trigger={o['triggerPrice']:<10} "
              f"qty={o['quantity']} reduceOnly={o['reduceOnly']} "
              f"closePos={o['closePosition']} algoId={o['algoId']} {ct.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
