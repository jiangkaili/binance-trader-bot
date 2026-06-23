"""List all open orders + positions across all symbols. Read-only."""
from __future__ import annotations

import hashlib
import hmac
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


def signed_get(path: str, params: dict | None = None) -> list | dict:
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    q = urlencode(params)
    sig = hmac.new(_SEC, q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{q}&signature={sig}"
    r = requests.get(url, headers={"X-MBX-APIKEY": KEY}, timeout=10)
    r.raise_for_status()
    return r.json()


def signed_delete(path: str, params: dict) -> dict:
    params = dict(params)
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    q = urlencode(params)
    sig = hmac.new(_SEC, q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{q}&signature={sig}"
    r = requests.delete(url, headers={"X-MBX-APIKEY": KEY}, timeout=10)
    return r.json()


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "list"

    print("=== OPEN ORDERS (all symbols) ===")
    orders = signed_get("/fapi/v1/openOrders")
    if not orders:
        print("(none)")
    for o in orders:
        print(f"  {o['symbol']:<10} {o['side']:<5} {o['type']:<18} qty={o['origQty']:<8} "
              f"stop={o.get('stopPrice', '-'):<10} price={o.get('price', '-'):<10} "
              f"reduceOnly={o.get('reduceOnly')} orderId={o['orderId']}")

    print()
    print("=== POSITIONS (non-zero only) ===")
    acct = signed_get("/fapi/v2/account")
    for p in acct["positions"]:
        amt = float(p["positionAmt"])
        if amt == 0:
            continue
        print(f"  {p['symbol']:<10} amt={amt:<10} entry={p['entryPrice']:<10} "
              f"mark={p.get('markPrice', '-'):<10} uPnl={p['unrealizedProfit']}")

    print()
    print(f"Wallet balance: {acct['totalWalletBalance']} USDT")
    print(f"Available     : {acct['availableBalance']} USDT")

    if action == "cancel-intc":
        print()
        print("=== CANCEL INTC orders ===")
        for o in orders:
            if o["symbol"] == "INTCUSDT":
                r = signed_delete("/fapi/v1/order",
                                  {"symbol": o["symbol"], "orderId": o["orderId"]})
                print(f"  cancelled orderId={o['orderId']}: {r.get('status', r)}")


if __name__ == "__main__":
    main()
