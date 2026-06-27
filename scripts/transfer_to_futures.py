"""Transfer USDT from Spot to USDT-M Futures (UNIVERSAL TRANSFER endpoint).

Usage:
    python scripts/transfer_to_futures.py 5         # transfer 5 USDT spot -> USDT-M futures / 转5 USDT从现货到USDT-M合约
    python scripts/transfer_to_futures.py 5 --yes   # skip confirmation prompt / 跳过确认提示

The universal transfer API uses type=UMFUTURE_MAIN to move from spot
to USDT-M futures.  See:
  POST /sapi/v1/asset/transfer
  type: MAIN_UMFUTURE  (spot -> USDT-M futures)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from gridtrader.quant.hmac_client import signed_request, BinanceTimestampError

HOSTS = {
    "testnet": "https://testnet.binance.vision",
    "prod":    "https://api.binance.com",
}


def load_env_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("amount", type=float, help="USDT amount to transfer spot -> USDT-M futures")
    p.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    p.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env.testnet"))
    args = p.parse_args()

    if args.amount <= 0:
        print("ERROR: amount must be > 0", file=sys.stderr)
        return 2

    load_env_file(args.env_file)
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
    base = HOSTS["testnet" if use_testnet else "prod"]

    if not api_key or not api_secret:
        print("ERROR: keys not set", file=sys.stderr)
        return 2

    print(f"Mode    : {'TESTNET' if use_testnet else 'PRODUCTION (real money)'}")
    print(f"Base    : {base}")
    print(f"From    : Spot wallet")
    print(f"To      : USDT-M Futures (UMFUTURE)")
    print(f"Amount  : {args.amount} USDT")
    print(f"Key     : ...{api_key[-4:]}  (redacted)")
    print()

    if not args.yes:
        print("This will move REAL USDT from your spot wallet to your futures wallet.")
        print("Type 'yes' to confirm, anything else to abort:")
        try:
            ans = input("> ")
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans.strip().lower() != "yes":
            print("Aborted.")
            return 0

    # Sync time / 同步时间
    state = {"offset": 0}
    r = requests.get(base + "/api/v3/time", timeout=10)
    r.raise_for_status()
    state["offset"] = int(r.json()["serverTime"]) - int(time.time() * 1000)

    # Universal transfer: spot -> USDT-M futures
    # type: MAIN_UMFUTURE
    # 通用划转：现货 -> USDT-M合约，类型：MAIN_UMFUTURE
    params = {
        "type": "MAIN_UMFUTURE",
        "asset": "USDT",
        "amount": args.amount,
    }

    print(f"\nSubmitting transfer...")
    try:
        r = signed_request("POST", base + "/sapi/v1/asset/transfer", params, api_key, api_secret,
                           time_offset_ms=state["offset"], timeout=15)
    except BinanceTimestampError:
        r = requests.get(base + "/api/v3/time", timeout=10)
        state["offset"] = int(r.json()["serverTime"]) - int(time.time() * 1000)
        r = signed_request("POST", base + "/sapi/v1/asset/transfer", params, api_key, api_secret,
                           time_offset_ms=state["offset"], timeout=15)

    print(f"HTTP {r.status_code}")
    if r.status_code == 200:
        j = r.json()
        print(f"  tranId : {j.get('tranId')}")
        print(f"  status : OK")
        print()
        print("Done. USDT should now be in your USDT-M Futures account.")
    else:
        print(f"  body: {r.text[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
