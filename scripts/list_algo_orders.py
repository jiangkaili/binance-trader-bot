"""Quick: list all open algo orders with createTime."""
import hashlib, hmac, os, sys, time
from pathlib import Path
from urllib.parse import urlencode
from datetime import datetime, timezone
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trader.config import load_env_file
load_env_file(os.getenv("ENV_FILE", ".env.testnet"))
KEY = os.environ["BINANCE_API_KEY"]
_SEC = os.environ["BINANCE_API_SECRET"].encode()

params = {"symbol": "BTCUSDT", "timestamp": int(time.time()*1000), "recvWindow": 5000}
q = urlencode(params)
sig = hmac.new(_SEC, q.encode(), hashlib.sha256).hexdigest()
r = requests.get(f"https://fapi.binance.com/fapi/v1/openAlgoOrders?{q}&signature={sig}",
                 headers={"X-MBX-APIKEY": KEY}, timeout=10).json()

for o in r if isinstance(r, list) else r.get("data", []):
    ct = datetime.fromtimestamp(o["createTime"]/1000, tz=timezone.utc)
    print(f"{o['orderType']:<22} side={o['side']} trigger={o['triggerPrice']} qty={o['quantity']} "
          f"reduceOnly={o['reduceOnly']} createTime={ct.isoformat()} algoId={o['algoId']}")
