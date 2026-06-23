"""Main Trader class — orchestrates Exchange + Strategy + Risk + State.

This replaces LiveTrader from scripts/live_trader.py.  The intent is
behavioral equivalence with the legacy class; differences should be
limited to dict→dataclass for Position and risk/exchange split.
"""
from __future__ import annotations

import signal as signal_lib
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gridtrader.quant.storage import Store
from gridtrader.quant.strategies import RsiRevertStrategy, Side

from .config import RuntimeContext, TraderConfig
from .exchange import BinanceFutures
from .logging_setup import make_logger
from .models import Position
from .paths import DRYRUN_LOG_PATH, LOG_PATH, TRADES_DB_PATH, ensure_data_dir
from .risk import RiskManager, RiskState
from .state import dump_state, load_pnl_state, save_pnl_state


class Trader:
    """Single-symbol futures trader.  See module docstring for design."""

    def __init__(self, ctx: RuntimeContext, cfg: TraderConfig):
        self.ctx = ctx
        self.cfg = cfg
        ensure_data_dir()
        # Dry-run runs in isolation: separate log file, no SQLite event writes,
        # separate state files. This way you can dry-run without polluting the
        # production bot's log, events table, or pnl_state.json.
        if ctx.dry_run:
            self.store = None
            log_path = DRYRUN_LOG_PATH
            store_event = None
        else:
            self.store = Store(str(TRADES_DB_PATH))
            log_path = LOG_PATH
            store_event = lambda level, msg: (self.store.log_event(level=level, msg=msg, strategy="live_trader"), None)[1]  # noqa: E731
        self.log = make_logger(log_path, store_event=store_event)
        self.exchange = BinanceFutures(
            api_key=ctx.api_key, api_secret=ctx.api_secret,
            base_url=ctx.base_url, symbol=cfg.symbol,
            dry_run=ctx.dry_run, log=self.log,
        )
        self.strategy = RsiRevertStrategy(
            period=cfg.rsi_period, oversold=cfg.rsi_oversold, overbought=cfg.rsi_overbought,
        )
        self.risk_state = RiskState()
        self.risk = RiskManager(cfg, self.risk_state, self.log)
        load_pnl_state(self.risk_state, self.log, dry_run=ctx.dry_run)

        # runtime
        self.position: Position | None = None
        self.last_signal: str = "FLAT"
        self.tick_count: int = 0
        self.last_action: str = "init"
        self._stop: bool = False
        self._banned_until: float = 0.0

    # ----- account bootstrap -----

    def _bootstrap_account(self) -> None:
        j = self.exchange.fetch_account()
        if not self.risk_state.starting_equity:
            eq = float(j.get("totalWalletBalance", 0))
            self.risk_state.starting_equity = eq
            self.log("INFO", f"starting_equity initialized = {eq:.4f} USDT")

    # ----- order helpers -----

    def _open(self, pos_side: str, qty: float, reason: str) -> None:
        qty = round(qty, 3)
        if qty <= 0:
            return
        api_side = "BUY" if pos_side == "LONG" else "SELL"
        self.exchange.cancel_all_orders()
        self.log("ACTION", f"OPEN {pos_side} {self.cfg.symbol} qty={qty} reason={reason}")
        r = self.exchange.market_order(api_side, qty)
        self.log("ACTION", f"order response: {r}")
        if isinstance(r, dict) and "error" not in r:
            entry = float(r.get("avgPrice", 0)) or 0
            if not entry:
                pos = self.exchange.get_position()
                if pos:
                    entry = pos.entry
            if entry:
                self.exchange.place_exchange_stops(
                    pos_side, entry,
                    self.cfg.stop_loss_pct, self.cfg.take_profit_pct, self.cfg.price_tick,
                )
        self.last_action = f"open_{pos_side.lower()} qty={qty}"

    def _close(self, reason: str) -> None:
        if not self.position:
            return
        p = self.position
        close_side = "SELL" if p.is_long else "BUY"
        self.exchange.cancel_all_orders()
        self.log("ACTION", f"CLOSE {p.side} {self.cfg.symbol} qty={p.qty:.3f} reason={reason}")
        r = self.exchange.market_order(close_side, p.qty, reduce_only=True)
        if isinstance(r, dict) and "error" in r:
            self.log("ERROR", f"close order FAILED (position kept): {r}")
            self.last_action = f"close FAILED reason={reason}"
            return
        self.log("ACTION", f"close response: {r}")
        pnl = p.u_pnl
        self.risk_state.daily_pnl += pnl
        self.risk_state.weekly_pnl += pnl
        if self.store is not None:
            try:
                self.store.log_trade(
                    symbol=self.cfg.symbol, side=close_side, price=p.mark, qty=p.qty,
                    source="paper" if self.ctx.dry_run else "live",
                    strategy=self.cfg.strategy_name, pnl=pnl,
                    order_id=str(r.get("orderId", "")) if isinstance(r, dict) else "",
                )
            except Exception as e:  # noqa: BLE001 — best-effort logging
                self.log("WARN", f"could not log trade to sqlite: {e}")
        self.risk.check_streak()
        self.position = None
        self.last_action = f"close reason={reason}"

    def _handle_external_close(self, prev_pos: Position) -> None:
        """Record a position closed by exchange-side SL/TP (between polls)."""
        close_side = "SELL" if prev_pos.is_long else "BUY"
        pnl = self.exchange.fetch_last_realized_pnl()
        if pnl == 0.0 and prev_pos.u_pnl:
            pnl = prev_pos.u_pnl
            self.log("WARN", f"income API returned 0 — using cached uPnl={pnl:+.4f} as fallback")
        self.log("ACTION",
                 f"EXTERNAL CLOSE {prev_pos.side} {self.cfg.symbol} qty={prev_pos.qty:.3f} "
                 f"entry={prev_pos.entry:.2f} realized_pnl={pnl:+.4f} (exchange SL/TP)")
        self.risk_state.daily_pnl += pnl
        self.risk_state.weekly_pnl += pnl
        if self.store is not None:
            try:
                self.store.log_trade(
                    symbol=self.cfg.symbol, side=close_side, price=prev_pos.mark, qty=prev_pos.qty,
                    source="live", strategy=self.cfg.strategy_name, pnl=pnl, order_id="exchange_sl_tp",
                )
            except Exception as e:  # noqa: BLE001
                self.log("WARN", f"could not log external close to sqlite: {e}")
        self.risk.check_streak()
        save_pnl_state(self.risk_state, self.log, dry_run=self.ctx.dry_run)

    # ----- main tick -----

    def tick(self) -> None:
        self.tick_count += 1
        self.exchange.maybe_resync_time()

        if self._banned_until and time.time() < self._banned_until:
            if self.tick_count % 12 == 0:
                self.log("WARN", f"IP banned (418), backing off {int(self._banned_until - time.time())}s")
            return
        self._banned_until = 0.0

        if not self.ctx.dry_run:
            self._bootstrap_account() if not self.risk_state.starting_equity else self.exchange.fetch_account()
        self.risk.reset_daily()

        if not self.ctx.dry_run:
            prev = self.position
            self.position = self.exchange.get_position()
            if prev is not None and self.position is None:
                self._handle_external_close(prev)

        # Bot-side mirror stops (in case exchange algo orders fail)
        if self.position and self.risk.hit_stop_loss(self.position):
            self._close("stop_loss")
            return
        if self.position and self.risk.hit_take_profit(self.position):
            self._close("take_profit")
            return

        df = self.exchange.get_klines(interval=self.cfg.kline_interval)
        if len(df) < self.cfg.warmup_bars:
            self.log("INFO", f"warmup — have {len(df)} bars, need {self.cfg.warmup_bars}")
            return

        bar = df.iloc[-1]
        sig = self.strategy.next_signal(bar, df)
        self.last_signal = sig.side.value

        ok, why = self.risk.can_open_new()
        if not ok and self.tick_count % 30 == 0:
            self.log("INFO", f"paused: {why}")

        # Signal-exit (optional)
        if not self.cfg.disable_signal_exit and self.position:
            if self.position.is_long and sig.side == Side.SELL:
                self._close(f"signal_sell ({sig.reason})")
                return
            if (not self.position.is_long) and sig.side == Side.BUY:
                self._close(f"signal_buy ({sig.reason})")
                return

        # Entries
        if (not self.position) and ok and sig.side in (Side.BUY, Side.SELL):
            mark = float(bar["close"])
            qty = (self.cfg.target_position_usdt * self.cfg.leverage) / mark
            pos_side = "LONG" if sig.side == Side.BUY else "SHORT"
            self._open(pos_side, qty, f"signal_{sig.side.value.lower()} ({sig.reason})")
            if not self.ctx.dry_run:
                self.position = self.exchange.get_position()
            return

        # Heartbeat every 10 ticks (~10 min)
        if self.tick_count % 10 == 0:
            if self.position is None:
                pos_str = "FLAT"
            else:
                p = self.position
                pos_str = (f"{p.side} qty={p.qty:.3f} entry={p.entry:.2f} "
                           f"mark={p.mark:.2f} uPnl={p.u_pnl:+.4f}")
            self.log("INFO",
                     f"heartbeat tick={self.tick_count} sig={sig.side.value} pos={pos_str} "
                     f"daily_pnl={self.risk_state.daily_pnl:+.4f} "
                     f"weekly_pnl={self.risk_state.weekly_pnl:+.4f} can_open={ok}")

        dump_state(self.cfg, self.risk_state, self.position,
                   self.tick_count, self.last_signal, self.ctx.dry_run, self.log)

    # ----- run loop -----

    def run(self) -> None:
        self.exchange.sync_time()
        if not self.ctx.dry_run:
            self._bootstrap_account()
        self.exchange.set_leverage(self.cfg.leverage)
        self.log("INFO",
                 f"STARTED symbol={self.cfg.symbol} leverage={self.cfg.leverage}x "
                 f"target={self.cfg.target_position_usdt} USDT "
                 f"stop={self.cfg.stop_loss_pct*100:.2f}%/pos "
                 f"daily_cap={self.cfg.daily_loss_pct*100:.1f}% "
                 f"weekly_cap={self.cfg.weekly_loss_pct*100:.1f}% "
                 f"starting_equity={self.risk_state.starting_equity:.4f} USDT "
                 f"dry_run={self.ctx.dry_run}")

        def _on_signal(signum, frame):
            self.log("INFO", f"received signal {signum}, will close position and exit")
            self._stop = True

        signal_lib.signal(signal_lib.SIGTERM, _on_signal)
        signal_lib.signal(signal_lib.SIGINT, _on_signal)

        while not self._stop:
            try:
                self.tick()
            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, "status_code", 0) if e.response else 0
                if status == 418:
                    self._banned_until = time.time() + 300
                    self.log("ERROR", "HTTP 418 (IP banned by Binance) — backing off 300s")
                else:
                    self.log("ERROR", f"tick failed: HTTPError {status}: {e}")
            except Exception as e:  # noqa: BLE001
                self.log("ERROR", f"tick failed: {type(e).__name__}: {e}")
            for _ in range(self.cfg.poll_seconds):
                if self._stop:
                    break
                time.sleep(1)

        try:
            if not self.ctx.dry_run:
                self.position = self.exchange.get_position()
                if self.position:
                    self._close("shutdown")
        except Exception as e:  # noqa: BLE001
            self.log("ERROR", f"shutdown close failed: {e}; please close manually in Binance UI")
        self.log("INFO",
                 f"EXITED cleanly. daily_pnl={self.risk_state.daily_pnl:+.4f} "
                 f"weekly_pnl={self.risk_state.weekly_pnl:+.4f}")
