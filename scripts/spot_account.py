"""Show SPOT account balances on Binance (read-only).

Default: testnet. Set USE_TESTNET=false for production.

Endpoints hit:
  GET /api/v3/account   — read spot balances (needs key with read permission)
"""
from __future__ import annotations

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


def cfg() -> tuple[str, dict | None, str, str]:
    env_file = os.getenv("ENV_FILE", ".env.testnet")
    load_env_file(env_file)
    use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
    base = HOSTS["testnet" if use_testnet else "prod"]

    proxy_host = os.getenv("PROXY_HOST", "").strip()
    proxy_port = os.getenv("PROXY_PORT", "0").strip()
    proxies = None
    if proxy_host and proxy_port not in ("", "0"):
        proxy = f"http://{proxy_host}:{proxy_port}"
        proxies = {"http": proxy, "https": proxy}

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    return base, proxies, api_key, api_secret


def main() -> int:
    base, proxies, api_key, api_secret = cfg()
    if not api_key or not api_secret:
        print("ERROR: BINANCE_API_KEY / BINANCE_API_SECRET not set.", file=sys.stderr)
        return 2

    print(f"Connecting to: {base}  (SPOT)")
    print(f"Proxies      : {proxies or 'direct'}")
    print(f"API key      : ...{api_key[-4:]}")
    print()

    # Sync time / 同步时间
    state = {"offset": 0}
    try:
        r = requests.get(base + "/api/v3/time", proxies=proxies, timeout=10)
        r.raise_for_status()
        server_ts = int(r.json()["serverTime"])
        state["offset"] = server_ts - int(time.time() * 1000)
        print(f"Server time  : {server_ts}   (local offset {state['offset']:+d} ms)")
    except Exception as e:
        print(f"WARN: could not fetch server time: {e}")

    def call(path: str):
        url = base + path
        try:
            return signed_request("GET", url, {}, api_key, api_secret,
                                  proxies=proxies, timeout=10,
                                  time_offset_ms=state["offset"])
        except BinanceTimestampError as e:
            try:
                r = requests.get(base + "/api/v3/time", proxies=proxies, timeout=10)
                state["offset"] = int(r.json()["serverTime"]) - int(time.time() * 1000)
            except Exception:
                pass
            return signed_request("GET", url, {}, api_key, api_secret,
                                  proxies=proxies, timeout=10,
                                  time_offset_ms=state["offset"])

    print()
    print("=" * 64)
    print("SPOT ACCOUNT (/api/v3/account)")
    print("=" * 64)
    try:
        r = call("/api/v3/account")
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text}")
            print()
            print("Likely causes:")
            print("  - Key has no SPOT read permission (your key was set up for futures only)")
            print("  - Key not whitelisted for this IP (103.151.172.96)")
            print("  - Wrong key type for this endpoint")
            return 1
        j = r.json()
        print(f"  accountType : {j.get('accountType')}")
        print(f"  canTrade    : {j.get('canTrade')}")
        print(f"  canWithdraw : {j.get('canWithdraw')}")
        print(f"  canDeposit  : {j.get('canDeposit')}")
        print(f"  permissions : {j.get('permissions')}")
        print()
        print("  Non-zero balances:")
        non_zero = [
            b for b in j.get("balances", [])
            if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
        ]
        if not non_zero:
            print("    (all zero — nothing deposited yet, or deposit still processing)")
        else:
            for b in non_zero:
                free = float(b["free"])
                locked = float(b["locked"])
                print(f"    {b['asset']:<8s}  free={free:>20,.8f}  locked={locked:>20,.8f}  total={free+locked:>20,.8f}")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 1
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
