"""Live trader: RSI extreme mean-reversion on Binance USDⓈ-M Futures.

Strategy and risk parameters are loaded from config/trader.yaml.
Environment variables (API keys, proxy) are loaded from .env.

USAGE
-----
    # Dry-run (read-only, no orders, no key needed for klines):
    python scripts/live_trader.py --dry-run

    # Live (real orders on mainnet):
    python scripts/live_trader.py

    # Run in background:
    nohup python scripts/live_trader.py > data/live_trader.log 2>&1 &

    # Graceful stop (closes position then exits):
    kill -TERM <pid>

    # Force stop (no guaranteed close — log a manual close in Binance UI):
    kill -KILL <pid>

OUTPUTS
-------
    data/live_trader.log      — human-readable log
    data/trades.db            — SQLite (orders, trades, events)
    data/live_trader.state    — JSON with last signal / position for inspection

EMERGENCY STOP
--------------
    kill -TERM <pid>           # script catches, closes position, exits
    Or manually in Binance UI  # then: kill -KILL <pid> to stop the script
    Or: touch data/KILLSWITCH  # bot will stop opening new positions
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
import yaml

# Make the project's quant package importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.hmac_client import BinanceTimestampError, signed_request
from gridtrader.quant.storage import Store
from gridtrader.quant.strategies import RsiRevertStrategy, Side
from gridtrader.quant.indicators import adx as calc_adx

# ===== Paths (single source of truth) =====
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "trader.yaml"
TRADES_DB_PATH = DATA_DIR / "trades.db"
LOG_PATH = DATA_DIR / "live_trader.log"
STATE_PATH = DATA_DIR / "live_trader.state"
PNL_STATE_PATH = DATA_DIR / "pnl_state.json"
KILLSWITCH_PATH = DATA_DIR / "KILLSWITCH"
COOLDOWN_PATH = DATA_DIR / "COOLDOWN_UNTIL"


# ===== CONFIG: load from config/trader.yaml =====

def _load_config() -> dict:
    """Load trading config from config/trader.yaml.

    Falls back to built-in defaults if file is missing.
    """
    if not CONFIG_PATH.exists():
        print(f"WARNING: {CONFIG_PATH} not found — using built-in defaults", file=sys.stderr)
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

_CFG = _load_config()

SYMBOL = _CFG.get("symbol", "BTCUSDT")
TARGET_POSITION_USDT = float(_CFG.get("target_position_usdt", 25.0))
LEVERAGE = int(_CFG.get("leverage", 20))
STRATEGY_NAME = _CFG.get("strategy_name", "rsi_extremes_5m")

# Strategy: RSI extreme mean-reversion
RSI_PERIOD = int(_CFG.get("rsi_period", 7))
RSI_OVERSOLD = float(_CFG.get("rsi_oversold", 20.0))
RSI_OVERBOUGHT = float(_CFG.get("rsi_overbought", 80.0))

# Risk management
STOP_LOSS_PCT_OF_POSITION = float(_CFG.get("stop_loss_pct", 0.01))
TAKE_PROFIT_PCT_OF_POSITION = float(_CFG.get("take_profit_pct", 0.01))
# v2 (2026-06-23): when True, opposite RSI signals do NOT close the position —
# winners are only taken at TP. This was the #1 cause of the reward/risk
# ratio collapsing to 0.2 in the original config.
DISABLE_SIGNAL_EXIT = bool(_CFG.get("disable_signal_exit", False))
DAILY_LOSS_PCT = float(_CFG.get("daily_loss_pct", 0.25))
WEEKLY_LOSS_PCT = float(_CFG.get("weekly_loss_pct", 0.40))

# Trend filter — block mean-reversion entries when ADX > threshold
TREND_FILTER_ENABLED = bool(_CFG.get("trend_filter_enabled", True))
TREND_FILTER_ADX_THRESHOLD = float(_CFG.get("trend_filter_adx_threshold", 25.0))

# v7: EMA trend-alignment filter — only long above EMA, only short below.
# Backtest showed RSI mean-reversion loses in trending markets because it
# fights the trend.  This filter aligns entries with the prevailing direction.
TREND_EMA_FILTER_ENABLED = bool(_CFG.get("trend_ema_filter_enabled", True))
TREND_EMA_PERIOD = int(_CFG.get("trend_ema_period", 200))

# Timing
STRATEGY_INTERVAL = _CFG.get("kline_interval", "5m")
POLL_SECONDS = int(_CFG.get("poll_seconds", 60))
WARMUP_BARS = int(_CFG.get("warmup_bars", 210))  # v7: 200 for EMA200 + buffer

HOSTS = {
    "testnet": "https://testnet.binancefuture.com",
    "prod":    "https://fapi.binance.com",
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


class LiveTrader:
    def __init__(self, api_key: str, api_secret: str, base_url: str,
                 dry_run: bool = False, log_path: str | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = base_url
        self.dry_run = dry_run
        self.symbol = SYMBOL
        self.strategy = RsiRevertStrategy(period=RSI_PERIOD, oversold=RSI_OVERSOLD, overbought=RSI_OVERBOUGHT)
        self.log_path = log_path or str(LOG_PATH)
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(str(TRADES_DB_PATH))

        # runtime state
        self.offset_ms = 0
        self.starting_equity: float = 0.0
        self.day_start_equity: float = 0.0
        self.week_start_equity: float = 0.0
        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.last_date: date = date.today()
        self.position: dict | None = None
        self.last_signal: str = "FLAT"
        self._stop: bool = False
        self.tick_count: int = 0
        self.last_action: str = "init"
        self._banned_until: float = 0.0
        self._load_pnl_state()
        # Re-check losing streak on startup so a restart does not silently
        # drop the 24h cooldown (the COOLDOWN_UNTIL file can be lost between
        # runs).  _check_streak is a no-op if the file is already active or
        # the streak is stale (>24h since last loss).
        self._check_streak()

    # ----- logging -----

    def log(self, level: str, msg: str) -> None:
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        line = f"[{ts}] [{level}] {msg}"
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"[log file write failed: {e}]", file=sys.stderr, flush=True)
        try:
            self.store.log_event(level=level, msg=msg, strategy="live_trader")
        except Exception as e:  # noqa: BLE001 — never let logging crash the loop
            print(f"[log DB write failed: {type(e).__name__}: {e}]", file=sys.stderr, flush=True)

    # ----- API -----

    def sync_time(self) -> None:
        r = requests.get(self.base + "/fapi/v1/time", timeout=10)
        r.raise_for_status()
        self.offset_ms = int(r.json()["serverTime"]) - int(time.time() * 1000)
        self._last_sync_ts = time.time()

    def _maybe_resync_time(self) -> None:
        """Auto-resync clock offset every 30 minutes to survive WSL clock drift.

        WSL's clock can drift by 200-500ms per minute after suspend/resume.
        Without periodic resync, long runs eventually exceed Binance's
        recvWindow (5000ms) and every signed request is rejected with -1021.
        """
        last = getattr(self, "_last_sync_ts", 0.0)
        if time.time() - last > 1800:  # 30 minutes
            try:
                old = self.offset_ms
                self.sync_time()
                if abs(self.offset_ms - old) > 500:
                    self.log("INFO", f"clock resync: offset {old}ms -> {self.offset_ms}ms")
            except Exception as e:
                self.log("WARN", f"periodic sync_time failed: {type(e).__name__}: {e}")

    def call(self, method: str, path: str, params: dict | None = None) -> requests.Response:
        p = params or {}
        url = self.base + path
        try:
            return signed_request(method, url, p, self.api_key, self.api_secret,
                                  time_offset_ms=self.offset_ms, timeout=10)
        except BinanceTimestampError:
            self.sync_time()
            return signed_request(method, url, p, self.api_key, self.api_secret,
                                  time_offset_ms=self.offset_ms, timeout=10)

    def fetch_account(self) -> dict:
        r = self.call("GET", "/fapi/v2/account")
        r.raise_for_status()
        j = r.json()
        if not self.starting_equity:
            self.starting_equity = float(j.get("totalWalletBalance", 0))
            self.day_start_equity = self.starting_equity
            self.week_start_equity = self.starting_equity
        return j

    def set_leverage(self) -> None:
        if self.dry_run:
            self.log("INFO", f"[DRY-RUN] would set leverage to {LEVERAGE}x")
            return
        r = self.call("POST", "/fapi/v1/leverage",
                      {"symbol": self.symbol, "leverage": LEVERAGE})
        if r.status_code == 200:
            self.log("INFO", f"leverage set to {LEVERAGE}x for {self.symbol}")
        else:
            self.log("WARN", f"set leverage failed: HTTP {r.status_code} {r.text[:200]}")

    def get_klines(self, interval: str = STRATEGY_INTERVAL, limit: int = 300):
        # Klines is a public endpoint — can be called without signature.
        # v7: increased from 100 to 300 for EMA200 warmup.
        r = requests.get(
            self.base + "/fapi/v1/klines",
            params={"symbol": self.symbol, "interval": interval, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        import pandas as pd
        rows = r.json()
        df = pd.DataFrame(rows, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time")
        return df[["open", "high", "low", "close", "volume"]].astype(float)

    def get_position(self) -> dict | None:
        r = self.call("GET", "/fapi/v2/positionRisk", {"symbol": self.symbol})
        r.raise_for_status()
        for p in r.json():
            if p["symbol"] == self.symbol:
                amt = float(p["positionAmt"])
                if abs(amt) > 1e-9:
                    return {
                        "side": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry": float(p["entryPrice"]),
                        "mark": float(p["markPrice"]),
                        "uPnl": float(p["unRealizedProfit"]),
                        "leverage": p["leverage"],
                    }
        return None

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
        r = self.call("POST", "/fapi/v1/order", params)
        if r.status_code == 200:
            return r.json()
        return {"error": r.text, "status_code": r.status_code}

    def cancel_all_orders(self) -> None:
        """Cancel all open orders (regular + algo) for this symbol."""
        if self.dry_run:
            return
        # Cancel regular orders
        r1 = self.call("DELETE", "/fapi/v1/allOpenOrders", {"symbol": self.symbol})
        # Cancel algo orders (TP/SL conditional orders live here since Binance 2025-12-09 change)
        r2 = self.call("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": self.symbol})
        self.log("INFO", f"cancel orders: regular HTTP {r1.status_code}, algo HTTP {r2.status_code}")

    def place_exchange_stops(self, pos_side: str, entry_price: float) -> None:
        """Place STOP_MARKET + TAKE_PROFIT_MARKET on exchange via algoOrder endpoint.
        Survives bot crash — Binance executes them server-side.
        Note: Binance moved conditional orders to /fapi/v1/algoOrder on 2025-12-09."""
        if self.dry_run:
            return
        # Round to BTCUSDT tick size = 0.1
        if pos_side == "LONG":
            sl_side = "SELL"
            sl_price = round(entry_price * (1 - STOP_LOSS_PCT_OF_POSITION), 1)
            tp_side = "SELL"
            tp_price = round(entry_price * (1 + TAKE_PROFIT_PCT_OF_POSITION), 1)
        else:  # SHORT
            sl_side = "BUY"
            sl_price = round(entry_price * (1 + STOP_LOSS_PCT_OF_POSITION), 1)
            tp_side = "BUY"
            tp_price = round(entry_price * (1 - TAKE_PROFIT_PCT_OF_POSITION), 1)

        # Stop-loss (algoOrder)
        r_sl = self.call("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": self.symbol,
            "side": sl_side,
            "type": "STOP_MARKET",
            "triggerPrice": str(sl_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        })
        if r_sl.status_code == 200:
            algo_id = r_sl.json().get("algoId", "?")
            self.log("ACTION", f"EXCHANGE STOP_LOSS placed: {sl_side} @ {sl_price} algoId={algo_id}")
        else:
            self.log("ERROR", f"EXCHANGE STOP_LOSS failed: HTTP {r_sl.status_code} {r_sl.text}")

        # Take-profit (algoOrder)
        r_tp = self.call("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL",
            "symbol": self.symbol,
            "side": tp_side,
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": str(tp_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        })
        if r_tp.status_code == 200:
            algo_id = r_tp.json().get("algoId", "?")
            self.log("ACTION", f"EXCHANGE TAKE_PROFIT placed: {tp_side} @ {tp_price} algoId={algo_id}")
        else:
            self.log("ERROR", f"EXCHANGE TAKE_PROFIT failed: HTTP {r_tp.status_code} {r_tp.text}")

    # ----- risk -----

    def reset_daily(self) -> None:
        today = date.today()
        if today != self.last_date:
            self.last_date = today
            self.day_start_equity = self.starting_equity
            self.daily_pnl = 0.0
            if today.weekday() == 0:  # Monday
                self.week_start_equity = self.starting_equity
                self.weekly_pnl = 0.0
            self.log("INFO", f"new day/week — daily/weekly counters reset")

    def can_open_new(self) -> tuple[bool, str]:
        # Kill-switch: human or self-imposed permanent stop
        if KILLSWITCH_PATH.exists():
            try:
                reason = KILLSWITCH_PATH.read_text().strip()[:200]
            except OSError as e:
                self.log("WARN", f"killswitch file unreadable: {e}")
                reason = "(unknown)"
            return False, f"KILLSWITCH active: {reason}"
        # 24h cooldown after 3 consecutive losses
        if COOLDOWN_PATH.exists():
            try:
                until = float(COOLDOWN_PATH.read_text().strip())
                if time.time() < until:
                    remaining = int(until - time.time())
                    return False, f"24h cooldown after losing streak: {remaining}s remaining"
                else:
                    COOLDOWN_PATH.unlink()
            except (OSError, ValueError) as e:
                self.log("WARN", f"cooldown file parse failed: {e}")
        daily_loss = -self.daily_pnl
        if daily_loss >= DAILY_LOSS_PCT * self.starting_equity:
            return False, f"daily loss cap hit: -${daily_loss:.4f} >= ${DAILY_LOSS_PCT * self.starting_equity:.4f}"
        weekly_loss = -self.weekly_pnl
        if weekly_loss >= WEEKLY_LOSS_PCT * self.starting_equity:
            return False, f"weekly loss cap hit: -${weekly_loss:.4f} >= ${WEEKLY_LOSS_PCT * self.starting_equity:.4f}"
        # Permanent kill-switch at -10% cumulative (via SQLite trades.db).
        # Excludes backfilled rows (order_id LIKE 'backfilled_%') — those are
        # historical/external fills reconciled into the DB and must not count
        # against the bot's own session PnL, otherwise old manual losses
        # permanently poison the kill-switch.
        try:
            import sqlite3
            c = sqlite3.connect(str(TRADES_DB_PATH))
            cum_pnl = c.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades "
                "WHERE order_id IS NULL OR order_id NOT LIKE 'backfilled_%'"
            ).fetchone()[0]
            c.close()
            if cum_pnl <= -0.10 * self.starting_equity:
                KILLSWITCH_PATH.write_text(
                    f"auto-killswitch: cumulative pnl {cum_pnl:.4f} <= -10% of {self.starting_equity}"
                )
                self.log("CRITICAL", f"AUTO KILL-SWITCH: cumulative loss {cum_pnl:.4f} hit -10% of starting equity (excl. backfilled)")
                return False, f"auto kill-switch triggered: cum_pnl={cum_pnl:.4f}"
        except Exception as e:
            self.log("WARN", f"could not check cumulative pnl: {e}")
        if self.starting_equity <= 0:
            return False, "no equity"
        return True, "ok"

    def check_position_stop_loss(self) -> bool:
        if not self.position:
            return False
        p = self.position
        if p["side"] == "LONG":
            change = (p["mark"] - p["entry"]) / p["entry"]
        else:
            change = (p["entry"] - p["mark"]) / p["entry"]
        if change < -STOP_LOSS_PCT_OF_POSITION:
            self.log("WARNING", f"STOP-LOSS hit: {self.symbol} {p['side']} change={change*100:.3f}% < -{STOP_LOSS_PCT_OF_POSITION*100:.2f}%; uPnl={p['uPnl']:.4f}")
            return True
        return False

    def check_position_take_profit(self) -> bool:
        if not self.position:
            return False
        p = self.position
        if p["side"] == "LONG":
            change = (p["mark"] - p["entry"]) / p["entry"]
        else:
            change = (p["entry"] - p["mark"]) / p["entry"]
        if change >= TAKE_PROFIT_PCT_OF_POSITION:
            self.log("INFO", f"TAKE-PROFIT hit: {self.symbol} {p['side']} change={change*100:.3f}% >= +{TAKE_PROFIT_PCT_OF_POSITION*100:.2f}%; uPnl={p['uPnl']:.4f}")
            return True
        return False

    # ----- actions -----

    def open_long(self, qty: float, reason: str) -> None:
        qty = round(qty, 3)
        if qty <= 0:
            return
        self.cancel_all_orders()  # clean any leftover conditional orders
        self.log("ACTION", f"OPEN LONG {self.symbol} qty={qty} reason={reason}")
        r = self.market_order("BUY", qty)
        self.log("ACTION", f"order response: {r}")
        if isinstance(r, dict) and "error" not in r:
            entry = float(r.get("avgPrice", 0)) or 0
            if not entry:
                # Market order — fetch entry from position
                pos = self.get_position()
                if pos:
                    entry = pos["entry"]
            if entry:
                self.place_exchange_stops("LONG", entry)
        self.last_action = f"open_long qty={qty}"

    def open_short(self, qty: float, reason: str) -> None:
        qty = round(qty, 3)
        if qty <= 0:
            return
        self.cancel_all_orders()  # clean any leftover conditional orders
        self.log("ACTION", f"OPEN SHORT {self.symbol} qty={qty} reason={reason}")
        r = self.market_order("SELL", qty)
        self.log("ACTION", f"order response: {r}")
        if isinstance(r, dict) and "error" not in r:
            entry = float(r.get("avgPrice", 0)) or 0
            if not entry:
                pos = self.get_position()
                if pos:
                    entry = pos["entry"]
            if entry:
                self.place_exchange_stops("SHORT", entry)
        self.last_action = f"open_short qty={qty}"

    def close_position(self, reason: str) -> None:
        if not self.position:
            return
        p = self.position
        close_side = "SELL" if p["side"] == "LONG" else "BUY"
        # Cancel exchange-side conditional orders before closing (avoid leftover triggers)
        self.cancel_all_orders()
        self.log("ACTION", f"CLOSE {p['side']} {self.symbol} qty={p['qty']:.3f} reason={reason}")
        r = self.market_order(close_side, p["qty"], reduce_only=True)
        # Fix #2: don't accumulate pnl or clear position if close failed
        if isinstance(r, dict) and "error" in r:
            self.log("ERROR", f"close order FAILED (position kept): {r}")
            self.last_action = f"close FAILED reason={reason}"
            return
        self.log("ACTION", f"close response: {r}")
        pnl = p["uPnl"]
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        try:
            self.store.log_trade(
                symbol=self.symbol,
                side=close_side,
                price=p["mark"],
                qty=p["qty"],
                source="paper" if self.dry_run else "live",
                strategy=STRATEGY_NAME,
                pnl=pnl,
                order_id=str(r.get("orderId", "")) if isinstance(r, dict) else "",
            )
        except Exception as e:
            self.log("WARN", f"could not log trade to sqlite: {e}")
        # Streak detection: 3 consecutive losses → 24h cooldown
        self._check_streak()
        self.position = None
        self.last_action = f"close reason={reason}"
        self._save_pnl_state()

    def _fetch_last_realized_pnl(self) -> float:
        """Fetch the most recent REALIZED_PNL from the Binance income endpoint."""
        try:
            r = self.call("GET", "/fapi/v1/income", {
                "symbol": self.symbol, "incomeType": "REALIZED_PNL", "limit": "3",
            })
            if r.status_code == 200:
                entries = r.json()
                if entries:
                    # Binance returns oldest-first; last entry is most recent
                    return float(entries[-1].get("income", 0))
        except Exception as e:
            self.log("WARN", f"could not fetch realized pnl from income: {e}")
        return 0.0

    def _check_streak(self) -> None:
        """Check for 3 consecutive losing trades → 24h cooldown.

        Also called from __init__ on startup so a restart does not silently
        drop an active cooldown — the COOLDOWN_UNTIL file can be lost if the
        data dir is cleaned, the process is killed mid-write, or a prior ops
        session removed it.  Without this re-check, 3 consecutive losses in
        the DB leave the bot free to open new trades after a restart, which
        defeats the streak-protection circuit breaker.

        Guards:
          - If the cooldown file already exists and is still active, return
            immediately (don't reset the countdown on every restart).
          - If the most recent loss is >24h old, the cooldown has already
            been served — don't re-trigger a stale streak on restart.
        """
        try:
            # Don't reset an already-active cooldown
            if COOLDOWN_PATH.exists():
                try:
                    if time.time() < float(COOLDOWN_PATH.read_text().strip()):
                        return  # cooldown already active — leave it alone
                except (OSError, ValueError):
                    pass  # corrupt/unreadable file — fall through and re-evaluate
            import sqlite3
            c = sqlite3.connect(str(TRADES_DB_PATH))
            recent = c.execute(
                "SELECT pnl, ts FROM trades WHERE source='live' ORDER BY ts DESC LIMIT 3"
            ).fetchall()
            c.close()
            if len(recent) == 3 and all(float(r[0]) < 0 for r in recent):
                # Skip stale streaks: if the most recent loss is >24h old the
                # cooldown has already been served — don't re-trigger on restart.
                from datetime import datetime, timezone
                try:
                    last_ts = datetime.fromisoformat(recent[0][1].replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - last_ts).total_seconds() > 86400:
                        return
                except Exception:
                    pass  # can't parse ts — be conservative and trigger cooldown
                until = time.time() + 86400
                COOLDOWN_PATH.write_text(str(until))
                self.log("CRITICAL", f"3 CONSECUTIVE LOSSES — 24h COOLDOWN until {time.strftime('%F %T', time.localtime(until))}")
        except Exception as e:
            self.log("WARN", f"streak check failed: {e}")

    def _handle_external_close(self, prev_pos: dict) -> None:
        """Record a position closed by exchange-side SL/TP (not by the bot).

        The exchange's STOP_MARKET / TAKE_PROFIT_MARKET fires between polls,
        so the bot's next get_position() returns None.  Without this method
        the realized loss is invisible to daily/weekly caps, the -10% auto
        kill-switch, and streak detection — risk management runs blind.

        We fetch the exact realized P&L from /fapi/v1/income (REALIZED_PNL)
        and log it just like a bot-initiated close.
        """
        side = prev_pos["side"]
        close_side = "SELL" if side == "LONG" else "BUY"
        pnl = self._fetch_last_realized_pnl()
        # Fallback: use cached uPnl if income API returned 0
        if pnl == 0.0 and prev_pos.get("uPnl"):
            pnl = prev_pos["uPnl"]
            self.log("WARN", f"income API returned 0 — using cached uPnl={pnl:+.4f} as fallback")
        self.log("ACTION", f"EXTERNAL CLOSE {side} {self.symbol} qty={prev_pos['qty']:.3f} "
                 f"entry={prev_pos['entry']:.2f} realized_pnl={pnl:+.4f} (exchange SL/TP)")
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        try:
            self.store.log_trade(
                symbol=self.symbol, side=close_side, price=prev_pos["mark"],
                qty=prev_pos["qty"], source="live", strategy=STRATEGY_NAME,
                pnl=pnl, order_id="exchange_sl_tp",
            )
        except Exception as e:
            self.log("WARN", f"could not log external close to sqlite: {e}")
        self._check_streak()
        self._save_pnl_state()

    # ----- state dump -----

    def dump_state(self) -> None:
        """Write current state to JSON so user can inspect on return."""
        state = {
            "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "tick": self.tick_count,
            "signal": self.last_signal,
            "starting_equity": self.starting_equity,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "position": self.position,
            "dry_run": self.dry_run,
            "strategy": STRATEGY_NAME,
            "constraints": {
                "leverage": LEVERAGE,
                "target_position_usdt": TARGET_POSITION_USDT,
                "stop_loss_pct": STOP_LOSS_PCT_OF_POSITION,
                "take_profit_pct": TAKE_PROFIT_PCT_OF_POSITION,
                "daily_loss_pct": DAILY_LOSS_PCT,
                "weekly_loss_pct": WEEKLY_LOSS_PCT,
            },
        }
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            self.log("WARN", f"could not dump state: {e}")
        self._save_pnl_state()

    # ----- pnl persistence (survive restarts) -----

    def _load_pnl_state(self) -> None:
        """Load daily/weekly pnl from disk so risk caps survive restarts."""
        try:
            with open(PNL_STATE_PATH, "r") as f:
                s = json.load(f)
            today = date.today()
            if s.get("date") == today.isoformat():
                self.daily_pnl = float(s.get("daily_pnl", 0.0))
            iso = today.isocalendar()
            current_week = f"{iso[0]}-{iso[1]}"
            if s.get("week") == current_week:
                self.weekly_pnl = float(s.get("weekly_pnl", 0.0))
            if self.daily_pnl or self.weekly_pnl:
                self.log("INFO", f"restored pnl state: daily={self.daily_pnl:+.4f} weekly={self.weekly_pnl:+.4f}")
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            pass

    def _save_pnl_state(self) -> None:
        try:
            today = date.today()
            iso = today.isocalendar()
            with open(PNL_STATE_PATH, "w") as f:
                json.dump({
                    "date": today.isoformat(),
                    "week": f"{iso[0]}-{iso[1]}",
                    "daily_pnl": self.daily_pnl,
                    "weekly_pnl": self.weekly_pnl,
                }, f)
        except OSError as e:
            self.log("WARN", f"pnl_state save failed: {e}")

    # ----- main loop -----

    def tick(self) -> None:
        self.tick_count += 1
        self._maybe_resync_time()

        # IP ban backoff (HTTP 418 from Binance)
        if self._banned_until and time.time() < self._banned_until:
            remaining = int(self._banned_until - time.time())
            if self.tick_count % 12 == 0:
                self.log("WARN", f"IP banned (418), backing off {remaining}s")
            return
        self._banned_until = 0.0

        if not self.dry_run:
            self.fetch_account()
        self.reset_daily()

        # Fetch position EARLY so stop-loss works even if klines fail later
        if not self.dry_run:
            prev_position = self.position
            self.position = self.get_position()
            # Detect exchange-side SL/TP closure (position vanished between polls).
            # This happens when the exchange's STOP_MARKET / TAKE_PROFIT_MARKET
            # fires between two 60s polls.  Without this check the realized loss
            # is never recorded and risk caps run blind.
            if prev_position is not None and self.position is None:
                self._handle_external_close(prev_position)

        # Stop-loss check BEFORE klines — don't let a klines failure skip it
        if self.check_position_stop_loss():
            self.close_position("stop_loss")
            return

        # Take-profit check
        if self.check_position_take_profit():
            self.close_position("take_profit")
            return

        df = self.get_klines()
        if len(df) < WARMUP_BARS:
            self.log("INFO", f"warmup — have {len(df)} bars, need {WARMUP_BARS}")
            return

        bar = df.iloc[-1]
        sig = self.strategy.next_signal(bar, df)
        self.last_signal = sig.side.value

        ok, why = self.can_open_new()

        # Trend filter: skip new entries when market is trending strongly.
        # Mean-reversion (RSI extremes) gets chewed up in trends — RSI goes
        # oversold but price keeps falling.  ADX > 25 = strong trend.
        current_adx = 0.0
        if TREND_FILTER_ENABLED and not self.position:
            try:
                current_adx = float(calc_adx(df, period=14).iloc[-1])
                if current_adx > TREND_FILTER_ADX_THRESHOLD:
                    ok = False
                    why = f"trending (ADX={current_adx:.1f} > {TREND_FILTER_ADX_THRESHOLD})"
            except Exception as e:
                self.log("WARN", f"ADX computation failed: {type(e).__name__}: {e}")
        self.current_adx = current_adx

        # v7: EMA trend-alignment filter — only long above EMA, only short below.
        # Backtest showed RSI mean-reversion loses when fighting the trend.
        # This aligns entries with the prevailing direction.
        current_ema = 0.0
        if TREND_EMA_FILTER_ENABLED and not self.position:
            try:
                from gridtrader.quant.indicators import ema as calc_ema
                ema_series = calc_ema(df["close"], TREND_EMA_PERIOD)
                current_ema = float(ema_series.iloc[-1])
                mark = float(bar["close"])
                if sig.side == Side.BUY and mark < current_ema:
                    ok = False
                    why = f"bearish (price {mark:.0f} < EMA{TREND_EMA_PERIOD} {current_ema:.0f})"
                elif sig.side == Side.SELL and mark > current_ema:
                    ok = False
                    why = f"bullish (price {mark:.0f} > EMA{TREND_EMA_PERIOD} {current_ema:.0f})"
            except Exception as e:
                self.log("WARN", f"EMA computation failed: {type(e).__name__}: {e}")
        self.current_ema = current_ema

        if not ok and self.tick_count % 30 == 0:
            self.log("INFO", f"paused: {why}")

        # v2: signal-exit can be disabled so winners ride to TP (not closed at 0.2% profit)
        if not DISABLE_SIGNAL_EXIT:
            # Position LONG & signal SELL -> close
            if self.position and self.position["side"] == "LONG" and sig.side == Side.SELL:
                self.close_position(f"signal_sell ({sig.reason})")
                return
            # Position SHORT & signal BUY -> close
            if self.position and self.position["side"] == "SHORT" and sig.side == Side.BUY:
                self.close_position(f"signal_buy ({sig.reason})")
                return
        # No position & signal BUY -> open LONG
        if (not self.position) and sig.side == Side.BUY and ok:
            mark = float(bar["close"])
            qty = (TARGET_POSITION_USDT * LEVERAGE) / mark
            self.open_long(qty, f"signal_buy ({sig.reason})")
            if not self.dry_run:
                self.position = self.get_position()
            return
        # No position & signal SELL -> open SHORT
        if (not self.position) and sig.side == Side.SELL and ok:
            mark = float(bar["close"])
            qty = (TARGET_POSITION_USDT * LEVERAGE) / mark
            self.open_short(qty, f"signal_sell ({sig.reason})")
            if not self.dry_run:
                self.position = self.get_position()
            return

        # Heartbeat every 5 minutes
        if self.tick_count % 10 == 0:
            pos = self.position
            if pos is None:
                pos_str = "FLAT"
            else:
                pos_str = (
                    f"{pos['side']} qty={pos['qty']:.3f} "
                    f"entry={pos['entry']:.2f} mark={pos['mark']:.2f} "
                    f"uPnl={pos['uPnl']:+.4f}"
                )
            self.log(
                "INFO",
                f"heartbeat tick={self.tick_count} sig={sig.side.value} "
                f"pos={pos_str} "
                f"adx={getattr(self, 'current_adx', 0):.1f} "
                f"daily_pnl={self.daily_pnl:+.4f} weekly_pnl={self.weekly_pnl:+.4f} "
                f"can_open={ok}",
            )

        # Always dump state at end of tick
        self.dump_state()

    def run(self) -> None:
        self.sync_time()
        if not self.dry_run:
            self.fetch_account()
        self.set_leverage()
        self.log("INFO",
                 f"STARTED symbol={self.symbol} leverage={LEVERAGE}x "
                 f"target={TARGET_POSITION_USDT} USDT stop={STOP_LOSS_PCT_OF_POSITION*100:.2f}%/pos "
                 f"daily_cap={DAILY_LOSS_PCT*100:.1f}% weekly_cap={WEEKLY_LOSS_PCT*100:.1f}% "
                 f"starting_equity={self.starting_equity:.4f} USDT dry_run={self.dry_run}")

        def _on_signal(signum, frame):
            self.log("INFO", f"received signal {signum}, will close position and exit")
            self._stop = True

        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)

        while not self._stop:
            try:
                self.tick()
            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, "status_code", 0) if e.response else 0
                if status == 418:
                    self._banned_until = time.time() + 300
                    self.log("ERROR", f"HTTP 418 (IP banned by Binance) — backing off 300s")
                else:
                    self.log("ERROR", f"tick failed: HTTPError {status}: {e}")
            except Exception as e:
                self.log("ERROR", f"tick failed: {type(e).__name__}: {e}")
            for _ in range(POLL_SECONDS):
                if self._stop:
                    break
                time.sleep(1)

        # Graceful exit: close position (skip in dry-run since we never opened one)
        try:
            if not self.dry_run:
                self.position = self.get_position()
                if self.position:
                    self.close_position("shutdown")
        except Exception as e:
            self.log("ERROR", f"shutdown close failed: {e}; please close manually in Binance UI")
        self.log("INFO", f"EXITED cleanly. daily_pnl={self.daily_pnl:+.4f} weekly_pnl={self.weekly_pnl:+.4f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="don't place real orders, just log signals")
    p.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env.testnet"))
    args = p.parse_args()

    load_env_file(args.env_file)

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
    base = HOSTS["testnet" if use_testnet else "prod"]

    if not api_key or not api_secret:
        print("ERROR: BINANCE_API_KEY / BINANCE_API_SECRET not set in env file.", file=sys.stderr)
        return 2

    print(f"Mode : {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"Base : {base}")
    print(f"Env  : {args.env_file}")
    print(f"Key  : ...{api_key[-4:]}  (redacted)")
    print()

    if not args.dry_run:
        print("=" * 60)
        print("LIVE MODE — REAL MONEY AT RISK")
        print(f"  - Stop-loss -{STOP_LOSS_PCT_OF_POSITION*100:.1f}% / Take-profit +{TAKE_PROFIT_PCT_OF_POSITION*100:.1f}% per trade")
        print(f"  - Daily cap  -{DAILY_LOSS_PCT*100:.0f}% of starting equity")
        print(f"  - Weekly cap -{WEEKLY_LOSS_PCT*100:.0f}% of starting equity")
        print(f"  - Single position, {SYMBOL} only, {TARGET_POSITION_USDT} USDT, {LEVERAGE}x leverage")
        print("  - To stop gracefully:  kill -TERM <pid>")
        print("=" * 60)
        print()

    trader = LiveTrader(api_key, api_secret, base, dry_run=args.dry_run)
    trader.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
