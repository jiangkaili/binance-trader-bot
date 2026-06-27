"""Connectivity check for Binance testnet / production.

Usage:
    # Test testnet (default): / 测试测试网（默认）：
    python scripts/ping.py

    # Test production: / 测试主网：
    USE_TESTNET=false python scripts/ping.py

Exits 0 on success, 1 on failure. Prints latency and server time offset.
Requires nothing but the `requests` package.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

# Allow `python scripts/ping.py` from project root / 允许从项目根目录运行`python scripts/ping.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


HOSTS = {
    "testnet_spot":   "https://testnet.binance.vision",
    "testnet_futures":"https://testnet.binancefuture.com",
    "prod_spot":      "https://api.binance.com",
    "prod_futures":   "https://fapi.binance.com",
}

# Public endpoints (no key needed) for each host / 每个主机的公共端点（无需密钥）
PING_PATHS = {
    "testnet_spot":    "/api/v3/time",
    "testnet_futures": "/fapi/v1/time",
    "prod_spot":       "/api/v3/time",
    "prod_futures":    "/fapi/v1/time",
}


def pick_target() -> tuple[str, str, str]:
    use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
    market = os.getenv("MARKET", "futures").strip().lower()
    if use_testnet:
        key = "testnet_futures" if market == "futures" else "testnet_spot"
    else:
        key = "prod_futures" if market == "futures" else "prod_spot"
    return HOSTS[key], PING_PATHS[key], key


def build_proxies() -> dict | None:
    host = os.getenv("PROXY_HOST", "").strip()
    port = os.getenv("PROXY_PORT", "0").strip()
    if not host or port in ("", "0"):
        return None
    proxy = f"http://{host}:{port}"
    return {"http": proxy, "https": proxy}


def main() -> int:
    base, path, label = pick_target()
    url = base + path
    proxies = build_proxies()
    t0 = time.time()
    try:
        r = requests.get(url, proxies=proxies, timeout=10)
    except Exception as e:
        print(f"[FAIL] {label}")
        print(f"  url    : {url}")
        print(f"  proxies: {proxies or 'direct'}")
        print(f"  error  : {type(e).__name__}: {e}")
        print()
        print("If you cannot reach testnet.binance.vision directly, set")
        print("  PROXY_HOST=127.0.0.1  PROXY_PORT=<your port>")
        return 1
    latency_ms = (time.time() - t0) * 1000
    if r.status_code != 200:
        print(f"[FAIL] HTTP {r.status_code} from {url}")
        print(f"  body: {r.text[:200]}")
        return 1
    j = r.json()
    server_ts = int(j.get("serverTime", 0))
    local_ts = int(time.time() * 1000)
    offset = server_ts - local_ts
    print(f"[OK]   {label}")
    print(f"  url      : {url}")
    print(f"  proxies  : {proxies or 'direct'}")
    print(f"  latency  : {latency_ms:.0f} ms")
    print(f"  serverTs : {server_ts}")
    print(f"  localTs  : {local_ts}")
    print(f"  offset   : {offset:+d} ms (use this in signed requests to avoid clock drift)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
