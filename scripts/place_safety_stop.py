"""Idempotently place exchange-side STOP_MARKET + TAKE_PROFIT_MARKET for current position.

Uses trader.exchange.BinanceFutures — no duplicated signing.

Independent of the bot — even if the bot dies, the exchange enforces SL/TP.
If a reduceOnly STOP_MARKET / TAKE_PROFIT_MARKET already exists, that side
is skipped (no duplicate).

Usage:
    python scripts/place_safety_stop.py [--sl-pct 0.01] [--tp-pct 0.015]
    python scripts/place_safety_stop.py --no-tp     # only SL / 仅止损
    python scripts/place_safety_stop.py --dry-run   # show plan, don't submit / 显示计划，不提交
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trader.config import get_connection_config
from trader.exchange import BinanceFutures


def _log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def get_price_precision(base_url: str, symbol: str) -> int:
    info = requests.get(f"{base_url}/fapi/v1/exchangeInfo", timeout=10).json()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                    return max(0, -int(round(math.log10(tick))))
    return 2


def _kind(o: dict) -> str | None:
    """Algo orders use 'orderType'; legacy use 'type'."""
    return o.get("type") or o.get("orderType")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--sl-pct", type=float, default=0.01,
                    help="stop-loss as fraction of entry (default 0.01 = 1%%)")
    ap.add_argument("--tp-pct", type=float, default=0.015,
                    help="take-profit as fraction of entry (default 0.015 = 1.5%%)")
    ap.add_argument("--no-tp", action="store_true", help="only place SL, skip TP")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't submit, just show what would be placed")
    args = ap.parse_args()

    env_file = os.getenv("ENV_FILE", ".env")
    base_url, proxies, api_key, api_secret = get_connection_config(env_file, market="futures")
    ex = BinanceFutures(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        symbol=args.symbol,
        dry_run=args.dry_run,
        log=_log,
    )
    ex.sync_time()

    pos = ex.get_position()
    if pos is None:
        print(f"NO POSITION on {args.symbol} — nothing to protect.")
        return 0

    is_long = pos.side == "LONG"
    entry = pos.entry
    qty = pos.qty
    close_side = "SELL" if is_long else "BUY"

    if is_long:
        sl_price = entry * (1 - args.sl_pct)
        tp_price = entry * (1 + args.tp_pct)
    else:
        sl_price = entry * (1 + args.sl_pct)
        tp_price = entry * (1 - args.tp_pct)

    prec = get_price_precision(base_url, args.symbol)
    sl_price = round(sl_price, prec)
    tp_price = round(tp_price, prec)

    print(f"Position : {args.symbol} {pos.side} qty={qty} entry={entry}")
    print(f"SL plan  : reduceOnly STOP_MARKET        {close_side} trigger={sl_price}  "
          f"({'-' if is_long else '+'}{args.sl_pct*100:.2f}%)")
    if not args.no_tp:
        print(f"TP plan  : reduceOnly TAKE_PROFIT_MARKET {close_side} trigger={tp_price}  "
              f"({'+' if is_long else '-'}{args.tp_pct*100:.2f}%)")

    # Existing protections (both legacy + algo endpoints) / 现有保护（包括旧版和算法端点）
    existing = ex.get_open_orders() + ex.get_open_algo_orders()
    existing_sl = [o for o in existing
                   if _kind(o) == "STOP_MARKET"
                   and (o.get("reduceOnly") or o.get("closePosition"))]
    existing_tp = [o for o in existing
                   if _kind(o) == "TAKE_PROFIT_MARKET"
                   and (o.get("reduceOnly") or o.get("closePosition"))]

    print()
    if existing_sl:
        print("Existing SL (will SKIP new SL):")
        for o in existing_sl:
            tp = o.get("stopPrice") or o.get("triggerPrice") or "?"
            oid = o.get("orderId") or o.get("algoId") or "?"
            print(f"  side={o['side']} trigger={tp} id={oid}")
    if existing_tp:
        print("Existing TP (will SKIP new TP):")
        for o in existing_tp:
            tp = o.get("stopPrice") or o.get("triggerPrice") or "?"
            oid = o.get("orderId") or o.get("algoId") or "?"
            print(f"  side={o['side']} trigger={tp} id={oid}")

    if args.dry_run:
        print()
        print("[DRY-RUN] no orders submitted.")
        return 0

    placed = []
    if not existing_sl:
        r = ex.place_algo_stop(side=close_side, order_type="STOP_MARKET",
                               trigger_price=sl_price, qty=qty)
        print()
        print("SL result:", r)
        placed.append(("SL", r))
    if not args.no_tp and not existing_tp:
        r = ex.place_algo_stop(side=close_side, order_type="TAKE_PROFIT_MARKET",
                               trigger_price=tp_price, qty=qty)
        print("TP result:", r)
        placed.append(("TP", r))

    if not placed:
        print()
        print("Nothing placed (both sides already protected).")
        return 0
    return 0 if all("algoId" in r for _, r in placed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
