"""Show futures account info and current positions on Binance.

Reads BINANCE_API_KEY / BINANCE_API_SECRET from env (NOT hardcoded).
Defaults to testnet. Set USE_TESTNET=false for production.

Outputs:
  - Account summary (total wallet, unrealized PnL, margin)
  - Open positions (symbol, qty, entry, mark, unrealized PnL)
  - Open orders (working, not yet filled)

Usage:
    # After you have filled in .env and `set -a; source .env; set +a`
    python scripts/positions_futures.py
    # Or pass an env file explicitly
    ENV_FILE=.env.testnet python scripts/positions_futures.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from gridtrader.quant.hmac_client import signed_request, BinanceTimestampError

HOSTS = {
    "testnet": "https://testnet.binancefuture.com",
    "prod":    "https://fapi.binance.com",
}


def load_env_file(path: str) -> None:
    """Lightweight .env loader (avoids the python-dotenv dep for this one script)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # do not overwrite an explicit env var
        os.environ.setdefault(k, v)


def cfg() -> tuple[str, dict | None, str, str]:
    """Return (base_url, proxies, api_key, api_secret)."""
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


def fetch_server_time(base: str, proxies: dict | None) -> int:
    r = requests.get(base + "/fapi/v1/time", proxies=proxies, timeout=10)
    r.raise_for_status()
    return int(r.json()["serverTime"])


def fmt_money(x, asset: str = "USDT") -> str:
    try:
        return f"{float(x):>14,.2f} {asset}"
    except (TypeError, ValueError):
        return f"{str(x):>14} {asset}"


def main() -> int:
    base, proxies, api_key, api_secret = cfg()
    if not api_key or not api_secret:
        print("ERROR: BINANCE_API_KEY / BINANCE_API_SECRET not set.", file=sys.stderr)
        print(f"  - Fill in your env file (current: ENV_FILE={os.getenv('ENV_FILE', '.env.testnet')})", file=sys.stderr)
        print(f"  - Or export them in the current shell", file=sys.stderr)
        return 2

    print(f"Connecting to: {base}")
    print(f"Proxies      : {proxies or 'direct'}")
    key_tail = api_key[-4:] if len(api_key) >= 4 else "????"
    print(f"API key      : ...{key_tail}  (redacted)")
    print()

    # Sync clock to server
    time_offset_ms = 0
    try:
        server_ts = fetch_server_time(base, proxies)
        time_offset_ms = server_ts - int(time.time() * 1000)
        print(f"Server time  : {server_ts}   (local offset {time_offset_ms:+d} ms)")
    except Exception as e:
        print(f"WARN: could not fetch server time: {e}")

    state = {"time_offset_ms": time_offset_ms}

    def call(method: str, path: str):
        """Call a signed endpoint, automatically retrying once on timestamp drift."""
        url = base + path
        try:
            return signed_request(method, url, {}, api_key, api_secret,
                                  proxies=proxies, timeout=10,
                                  time_offset_ms=state["time_offset_ms"])
        except BinanceTimestampError as e:
            print(f"  (timestamp drift detected, re-syncing... was {e.time_offset_ms} ms)")
            try:
                server_ts = fetch_server_time(base, proxies)
                state["time_offset_ms"] = server_ts - int(time.time() * 1000)
            except Exception:
                pass
            return signed_request(method, url, {}, api_key, api_secret,
                                  proxies=proxies, timeout=10,
                                  time_offset_ms=state["time_offset_ms"])

    # Account info
    print()
    print("=" * 64)
    print("ACCOUNT (/fapi/v2/account)")
    print("=" * 64)
    try:
        r = call("GET", "/fapi/v2/account")
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text}")
            return 1
        a = r.json()
        print(f"  totalWalletBalance : {fmt_money(a.get('totalWalletBalance'))}")
        print(f"  totalUnrealizedPnl : {fmt_money(a.get('totalUnrealizedProfit'))}")
        print(f"  totalMarginBalance : {fmt_money(a.get('totalMarginBalance'))}")
        print(f"  availableBalance   : {fmt_money(a.get('availableBalance'))}")
        print(f"  maxWithdrawAmount  : {fmt_money(a.get('maxWithdrawAmount'))}")
        assets = a.get("assets") or []
        non_zero = [x for x in assets if abs(float(x.get("walletBalance", 0))) > 1e-9]
        if non_zero:
            print(f"  assets             :")
            for x in non_zero:
                print(f"      {x['asset']:<6s}  wallet={float(x['walletBalance']):>14,.4f}  "
                      f"unrealized={float(x.get('unrealizedProfit', 0)):>10,.4f}  margin={float(x.get('marginBalance', 0)):>14,.4f}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return 1

    # Positions
    print()
    print("=" * 64)
    print("POSITIONS (/fapi/v2/positionRisk)")
    print("=" * 64)
    try:
        r = call("GET", "/fapi/v2/positionRisk")
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text}")
        else:
            positions = r.json()
            open_pos = [p for p in positions if abs(float(p.get("positionAmt", 0))) > 0]
            if not open_pos:
                print("  (no open positions)")
            else:
                print(f"  {'symbol':<10s}  {'side':<5s}  {'qty':>12s}  {'entry':>12s}  {'mark':>12s}  "
                      f"{'uPnl':>12s}  {'lev':>4s}  {'marginType':<10s}")
                for p in open_pos:
                    amt = float(p["positionAmt"])
                    side = "LONG" if amt > 0 else "SHORT"
                    print(f"  {p['symbol']:<10s}  {side:<5s}  {abs(amt):>12f}  "
                          f"{float(p.get('entryPrice', 0)):>12,.2f}  "
                          f"{float(p.get('markPrice', 0)):>12,.2f}  "
                          f"{float(p.get('unRealizedProfit', 0)):>12,.2f}  "
                          f"{p.get('leverage', '?'):>4}  "
                          f"{p.get('marginType', '?'):<10s}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")

    # Open orders
    print()
    print("=" * 64)
    print("OPEN ORDERS (/fapi/v1/openOrders)")
    print("=" * 64)
    try:
        r = call("GET", "/fapi/v1/openOrders")
        if r.status_code != 200:
            print(f"HTTP {r.status_code}: {r.text}")
        else:
            orders = r.json()
            if not orders:
                print("  (no open orders)")
            else:
                for o in orders:
                    print(f"  {o['symbol']:<10s}  {o['side']:<4s}  {o['type']:<10s}  "
                          f"qty={o['origQty']:>10s}  price={o.get('price','-'):>10s}  "
                          f"status={o['status']:<10s}  oid={o['orderId']}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
