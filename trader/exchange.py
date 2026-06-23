"""Binance USDⓈ-M Futures HTTP client — all REST calls live here.

Extracted from LiveTrader's 8 API methods.  No state beyond credentials
+ clock-offset; safe to recreate per session.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gridtrader.quant.hmac_client import BinanceTimestampError, signed_request

from .models import Position


class BinanceFutures:
    """Stateless-ish wrapper over Binance USDⓈ-M Futures REST API.

    Holds: api_key/secret, base URL, clock offset, dry-run flag, logger.
    All HTTP calls go through `_call` so timestamp drift is auto-recovered.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        symbol: str,
        dry_run: bool,
        log: Callable[[str, str], None],
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = base_url
        self.symbol = symbol
        self.dry_run = dry_run
        self.log = log
        self.offset_ms = 0
        self._last_sync_ts = 0.0

    # ----- time -----

    def sync_time(self) -> None:
        r = requests.get(self.base + "/fapi/v1/time", timeout=10)
        r.raise_for_status()
        self.offset_ms = int(r.json()["serverTime"]) - int(time.time() * 1000)
        self._last_sync_ts = time.time()

    def maybe_resync_time(self) -> None:
        """Re-sync clock every 30 minutes (WSL drifts after suspend/resume)."""
        if time.time() - self._last_sync_ts > 1800:
            try:
                old = self.offset_ms
                self.sync_time()
                if abs(self.offset_ms - old) > 500:
                    self.log("INFO", f"clock resync: offset {old}ms -> {self.offset_ms}ms")
            except Exception as e:  # noqa: BLE001 — non-fatal
                self.log("WARN", f"periodic sync_time failed: {type(e).__name__}: {e}")

    # ----- low-level call -----

    def _call(self, method: str, path: str, params: dict | None = None) -> requests.Response:
        p = params or {}
        url = self.base + path
        try:
            return signed_request(method, url, p, self.api_key, self.api_secret,
                                  time_offset_ms=self.offset_ms, timeout=10)
        except BinanceTimestampError:
            self.sync_time()
            return signed_request(method, url, p, self.api_key, self.api_secret,
                                  time_offset_ms=self.offset_ms, timeout=10)

    # ----- account / market -----

    def fetch_account(self) -> dict:
        r = self._call("GET", "/fapi/v2/account")
        r.raise_for_status()
        return r.json()

    def set_leverage(self, leverage: int) -> None:
        if self.dry_run:
            self.log("INFO", f"[DRY-RUN] would set leverage to {leverage}x")
            return
        r = self._call("POST", "/fapi/v1/leverage",
                       {"symbol": self.symbol, "leverage": leverage})
        if r.status_code == 200:
            self.log("INFO", f"leverage set to {leverage}x for {self.symbol}")
        else:
            self.log("WARN", f"set leverage failed: HTTP {r.status_code} {r.text[:200]}")

    def get_klines(self, interval: str = "5m", limit: int = 100):
        """Returns a pandas DataFrame indexed by open_time (public endpoint)."""
        import pandas as pd
        r = requests.get(
            self.base + "/fapi/v1/klines",
            params={"symbol": self.symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
        df = pd.DataFrame(rows, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def get_position(self) -> Position | None:
        r = self._call("GET", "/fapi/v2/positionRisk", {"symbol": self.symbol})
        r.raise_for_status()
        for p in r.json():
            if p["symbol"] == self.symbol:
                amt = float(p["positionAmt"])
                if abs(amt) > 1e-9:
                    return Position(
                        side="LONG" if amt > 0 else "SHORT",
                        qty=abs(amt),
                        entry=float(p["entryPrice"]),
                        mark=float(p["markPrice"]),
                        u_pnl=float(p["unRealizedProfit"]),
                        leverage=p["leverage"],
                    )
        return None

    def fetch_last_realized_pnl(self) -> float:
        """Sum REALIZED_PNL incomes from the last 5 minutes (for external close detection)."""
        try:
            since_ms = int((time.time() - 300) * 1000)
            r = self._call("GET", "/fapi/v1/income", {
                "symbol": self.symbol,
                "incomeType": "REALIZED_PNL",
                "startTime": since_ms,
                "limit": 50,
            })
            r.raise_for_status()
            return sum(float(item.get("income", 0)) for item in r.json())
        except Exception as e:  # noqa: BLE001
            self.log("WARN", f"could not fetch realized pnl from income: {e}")
            return 0.0

    def get_open_orders(self, symbol: str | None = None) -> list:
        """Regular open orders (LIMIT/MARKET/STOP). Does NOT include algo orders."""
        sym = symbol or self.symbol
        r = self._call("GET", "/fapi/v1/openOrders", {"symbol": sym})
        r.raise_for_status()
        return r.json()

    def get_open_algo_orders(self, symbol: str | None = None) -> list:
        """Algo (conditional) orders: STOP_MARKET / TAKE_PROFIT_MARKET / TRAILING_STOP_MARKET.

        Since Binance 2025-12-09 these live in a separate bucket and are
        NOT returned by /fapi/v1/openOrders — easy to mistake an
        algo-protected position for a naked one.
        """
        sym = symbol or self.symbol
        r = self._call("GET", "/fapi/v1/openAlgoOrders", {"symbol": sym})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            return data.get("data") or data.get("orders") or []
        return data

    def get_account_balance(self) -> dict:
        """Per-asset balance dict {asset: {"balance": x, "available": y}}."""
        acct = self.fetch_account()
        out: dict[str, dict] = {}
        for a in acct.get("assets", []):
            out[a["asset"]] = {
                "balance": float(a.get("walletBalance", 0)),
                "available": float(a.get("availableBalance", 0)),
            }
        return out

    # ----- orders -----

    def market_order(self, side: str, qty: float, reduce_only: bool = False) -> dict:
        if self.dry_run:
            return {"orderId": "DRY-RUN", "status": "DRY-RUN", "side": side, "qty": qty}
        params = {
            "symbol": self.symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        r = self._call("POST", "/fapi/v1/order", params)
        if r.status_code == 200:
            return r.json()
        return {"error": r.text, "status_code": r.status_code}

    def cancel_all_orders(self) -> None:
        """Cancel all open orders (regular + algo) for the bound symbol."""
        if self.dry_run:
            return
        r1 = self._call("DELETE", "/fapi/v1/allOpenOrders", {"symbol": self.symbol})
        # Algo (conditional) orders live in a separate bucket since Binance 2025-12-09.
        r2 = self._call("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": self.symbol})
        self.log("INFO", f"cancel orders: regular HTTP {r1.status_code}, algo HTTP {r2.status_code}")

    def place_algo_stop(
        self,
        side: str,
        order_type: str,
        trigger_price: float,
        qty: float | None = None,
        close_position: bool = False,
        working_type: str = "MARK_PRICE",
        reduce_only: bool = True,
    ) -> dict:
        """Low-level algo (conditional) order placement.

        Required since Binance 2025-12-09 migrated SL/TP/Trailing off /fapi/v1/order.

        order_type: STOP_MARKET | TAKE_PROFIT_MARKET | STOP | TAKE_PROFIT | TRAILING_STOP_MARKET
        side:       BUY (to close SHORT) | SELL (to close LONG)
        Either `qty` or `close_position=True` — not both.
        """
        if self.dry_run:
            return {"algoId": "DRY-RUN", "orderType": order_type, "side": side,
                    "triggerPrice": trigger_price, "quantity": qty}
        params: dict = {
            "algoType": "CONDITIONAL",
            "symbol": self.symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": str(trigger_price),
            "workingType": working_type,
        }
        if close_position:
            params["closePosition"] = "true"
        else:
            assert qty is not None, "qty required when close_position=False"
            params["quantity"] = qty
            if reduce_only:
                params["reduceOnly"] = "true"
        r = self._call("POST", "/fapi/v1/algoOrder", params)
        if r.status_code == 200:
            return r.json()
        return {"error": r.text, "status_code": r.status_code}

    def place_exchange_stops(
        self,
        pos_side: str,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        price_tick: float,
    ) -> None:
        """Place server-side STOP_MARKET + TAKE_PROFIT_MARKET (survives bot crash)."""
        if self.dry_run:
            return
        decimals = max(0, len(str(price_tick).split(".")[-1])) if "." in str(price_tick) else 0
        if pos_side == "LONG":
            sl_side = tp_side = "SELL"
            sl_price = round(entry_price * (1 - stop_loss_pct), decimals)
            tp_price = round(entry_price * (1 + take_profit_pct), decimals)
        else:
            sl_side = tp_side = "BUY"
            sl_price = round(entry_price * (1 + stop_loss_pct), decimals)
            tp_price = round(entry_price * (1 - take_profit_pct), decimals)

        r_sl = self._call("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": self.symbol, "side": sl_side,
            "type": "STOP_MARKET", "triggerPrice": str(sl_price),
            "closePosition": "true", "workingType": "MARK_PRICE",
        })
        if r_sl.status_code == 200:
            self.log("ACTION", f"EXCHANGE STOP_LOSS placed: {sl_side} @ {sl_price} algoId={r_sl.json().get('algoId','?')}")
        else:
            self.log("ERROR", f"EXCHANGE STOP_LOSS failed: HTTP {r_sl.status_code} {r_sl.text}")

        r_tp = self._call("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": self.symbol, "side": tp_side,
            "type": "TAKE_PROFIT_MARKET", "triggerPrice": str(tp_price),
            "closePosition": "true", "workingType": "MARK_PRICE",
        })
        if r_tp.status_code == 200:
            self.log("ACTION", f"EXCHANGE TAKE_PROFIT placed: {tp_side} @ {tp_price} algoId={r_tp.json().get('algoId','?')}")
        else:
            self.log("ERROR", f"EXCHANGE TAKE_PROFIT failed: HTTP {r_tp.status_code} {r_tp.text}")
