"""One-shot: place exchange-side STOP_MARKET + TAKE_PROFIT_MARKET safety net.

Reads current position from Binance, calculates SL/TP prices, places two
reduceOnly orders. This is independent of the bot — even if the bot dies,
the exchange will enforce both stop-loss AND take-profit.

Idempotent: if a reduceOnly STOP_MARKET / TAKE_PROFIT_MARKET already
exists for the symbol, that side is left alone (no duplicate).

Usage:
    python scripts/place_safety_stop.py [--sl-pct 0.01] [--tp-pct 0.015]
    python scripts/place_safety_stop.py --no-tp     # only SL, no TP
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import math
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trader.config import load_env_file

load_env_file(os.getenv("ENV_FILE", ".env.testnet"))
KEY = os.environ["BINANCE_API_KEY"]
_SEC = os.environ["BINANCE_API_SECRET"].encode()
BASE = "https://fapi.binance.com"


def _signed(method: str, path: str, params: dict | None = None) -> dict | list:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    q = urlencode(params)
    sig = hmac.new(_SEC, q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": KEY}
    r = requests.request(method, url, headers=headers, timeout=10)
    return r.json()


def get_position(symbol: str) -> dict | None:
    acct = _signed("GET", "/fapi/v2/account")
    for p in acct["positions"]:
        if p["symbol"] == symbol and float(p["positionAmt"]) != 0:
            return p
    return None


def get_open_orders(symbol: str) -> list:
    return _signed("GET", "/fapi/v1/openOrders", {"symbol": symbol})


def get_open_algo_orders(symbol: str) -> list:
    r = _signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
    if isinstance(r, dict):
        return r.get("data") or r.get("orders") or []
    return r


def get_price_precision(symbol: str) -> int:
    info = requests.get(f"{BASE}/fapi/v1/exchangeInfo", timeout=10).json()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                    return max(0, -int(round(math.log10(tick))))
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--sl-pct", type=float, default=0.01,
                    help="stop-loss as fraction of entry (default 0.01 = 1%%)")
    ap.add_argument("--tp-pct", type=float, default=0.015,
                    help="take-profit as fraction of entry (default 0.015 = 1.5%%)")
    ap.add_argument("--no-tp", action="store_true",
                    help="only place SL, skip TP")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't actually place orders, just print what would be placed")
    args = ap.parse_args()

    pos = get_position(args.symbol)
    if not pos:
        print(f"NO POSITION on {args.symbol} — nothing to protect.")
        return 0

    amt = float(pos["positionAmt"])
    entry = float(pos["entryPrice"])
    is_long = amt > 0
    qty = abs(amt)

    if is_long:
        sl_price = entry * (1 - args.sl_pct)
        tp_price = entry * (1 + args.tp_pct)
        close_side = "SELL"
    else:
        sl_price = entry * (1 + args.sl_pct)
        tp_price = entry * (1 - args.tp_pct)
        close_side = "BUY"

    prec = get_price_precision(args.symbol)
    sl_price = round(sl_price, prec)
    tp_price = round(tp_price, prec)

    print(f"Position : {args.symbol} {'LONG' if is_long else 'SHORT'} qty={qty} entry={entry}")
    print(f"SL plan  : reduceOnly STOP_MARKET        {close_side} stopPrice={sl_price}  ({'-' if is_long else '+'}{args.sl_pct*100:.2f}%)")
    if not args.no_tp:
        print(f"TP plan  : reduceOnly TAKE_PROFIT_MARKET {close_side} stopPrice={tp_price}  ({'+' if is_long else '-'}{args.tp_pct*100:.2f}%)")

    # Check existing reduceOnly stops on this symbol (both legacy + algo endpoints)
    legacy_orders = get_open_orders(args.symbol)
    algo_orders = get_open_algo_orders(args.symbol)
    # algo orders use "orderType"; legacy use "type"
    def _kind(o):
        return o.get("type") or o.get("orderType")
    existing_sl = [o for o in legacy_orders + algo_orders
                   if _kind(o) == "STOP_MARKET" and (o.get("reduceOnly") or o.get("closePosition"))]
    existing_tp = [o for o in legacy_orders + algo_orders
                   if _kind(o) == "TAKE_PROFIT_MARKET" and (o.get("reduceOnly") or o.get("closePosition"))]

    print()
    if existing_sl:
        print("Existing SL orders (will SKIP placing new SL):")
        for o in existing_sl:
            tp = o.get("stopPrice") or o.get("triggerPrice") or "?"
            oid = o.get("orderId") or o.get("algoId") or "?"
            print(f"  side={o['side']} trigger={tp} orderId={oid}")
    if existing_tp:
        print("Existing TP orders (will SKIP placing new TP):")
        for o in existing_tp:
            tp = o.get("stopPrice") or o.get("triggerPrice") or "?"
            oid = o.get("orderId") or o.get("algoId") or "?"
            print(f"  side={o['side']} trigger={tp} orderId={oid}")

    if args.dry_run:
        print()
        print("[DRY-RUN] would place above orders. Re-run without --dry-run to submit.")
        return 0

    placed = []
    if not existing_sl:
        r = _signed("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": args.symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "triggerPrice": sl_price,
            "quantity": qty,
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "true",
        })
        print()
        print("SL result:", r)
        placed.append(("SL", r))

    if not args.no_tp and not existing_tp:
        r = _signed("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": args.symbol,
            "side": close_side,
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": tp_price,
            "quantity": qty,
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "true",
        })
        print("TP result:", r)
        placed.append(("TP", r))

    if not placed:
        print()
        print("Nothing placed (both sides already exist).")
        return 0

    all_ok = all("orderId" in r for _, r in placed)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
