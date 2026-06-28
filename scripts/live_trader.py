"""Live trader: RSI extreme mean-reversion on Binance USDⓈ-M Futures.

Strategy and risk parameters are loaded from config/trader.yaml via TraderConfig.
All exchange API calls are delegated to BinanceFutures (trader/exchange.py) —
no duplicated signing, timestamp, or endpoint logic.
Environment variables (API keys) are loaded from .env via load_env_file.

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
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

# Make the project's packages importable / 使项目包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.indicators import adx as calc_adx, ema as calc_ema, funding_zscore as calc_funding_zscore
from gridtrader.quant.storage import Store
from gridtrader.quant.strategies import RsiRevertStrategy, Side

from trader.config import RuntimeContext, TraderConfig, load_env_file
from trader.exchange import BinanceFutures
from trader.models import Position
from trader.paths import (
    COOLDOWN_PATH, KILLSWITCH_PATH, LOG_PATH,
    PNL_STATE_PATH, STATE_PATH, TRADES_DB_PATH,
)


def _interval_seconds(interval: str) -> int:
    """Parse '5m' → 300, '1h' → 3600, '1d' → 86400."""
    if interval.endswith("m"):
        return int(interval[:-1]) * 60
    if interval.endswith("h"):
        return int(interval[:-1]) * 3600
    if interval.endswith("d"):
        return int(interval[:-1]) * 86400
    return 300


class LiveTrader:
    def __init__(self, api_key: str, api_secret: str, base_url: str,
                 dry_run: bool = False, log_path: str | None = None):
        self.cfg = TraderConfig.from_yaml()
        self.dry_run = dry_run
        self.symbol = self.cfg.symbol
        self.log_path = log_path or str(LOG_PATH)
        Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(str(TRADES_DB_PATH))

        # BinanceFutures handles all API calls — no duplicated signing/logic. / BinanceFutures处理所有API调用——无重复签名/逻辑。
        self.ex = BinanceFutures(
            api_key=api_key, api_secret=api_secret, base_url=base_url,
            symbol=self.symbol, dry_run=dry_run, log=self.log,
        )

        self.strategy = RsiRevertStrategy(
            period=self.cfg.rsi_period,
            oversold=self.cfg.rsi_oversold,
            overbought=self.cfg.rsi_overbought,
        )

        # runtime state / 运行时状态
        self.starting_equity: float = 0.0
        self.day_start_equity: float = 0.0
        self.week_start_equity: float = 0.0
        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.last_date: date = date.today()
        self.position: Position | None = None
        self.last_signal: str = "FLAT"
        self._stop: bool = False
        self.tick_count: int = 0
        self.last_action: str = "init"
        self._banned_until: float = 0.0
        self._cooldown_until: float = 0.0  # post-trade bar cooldown / 交易后K线冷却
        self.current_adx: float = 0.0
        self.current_ema: float = 0.0
        self.current_funding_z: float = 0.0  # v9: funding rate z-score / 资金费率Z-score
        self._funding_cache: list = []       # v9: cached funding rates / 缓存的资金费率
        self._funding_cache_ts: float = 0.0  # v9: cache timestamp / 缓存时间戳
        self._load_pnl_state()
        # Re-check losing streak on startup so a restart does not silently / 启动时重新检查连败防止重启后静默
        # drop the 24h cooldown. / 丢失24小时冷却。
        self._check_streak()

    # ----- logging ----- / ----- 日志 -----

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

    # ----- account ----- / ----- 账户 -----

    def fetch_account(self) -> dict:
        j = self.ex.fetch_account()
        if not self.starting_equity:
            self.starting_equity = float(j.get("totalWalletBalance", 0))
            self.day_start_equity = self.starting_equity
            self.week_start_equity = self.starting_equity
        return j

    # ----- funding rate (v9) ----- / ----- 资金费率 (v9) -----

    def _get_funding_zscore(self) -> float:
        """Fetch funding rate history and compute current z-score.

        Caches for 5 minutes to avoid hitting the API every tick (funding
        settles every 8h, so 5-min staleness is irrelevant).
        / 获取资金费率历史并计算当前Z-score。缓存5分钟（资金费率8h结算一次，5分钟延迟无影响）。
        """
        cfg = self.cfg
        if not cfg.funding_rate_enabled:
            return 0.0
        # Cache for 5 minutes / 缓存5分钟
        if self._funding_cache and (time.time() - self._funding_cache_ts) < 300:
            return self.current_funding_z
        try:
            df = self.ex.fetch_funding_rate_history(limit=cfg.funding_zscore_period + 10)
            if df.empty or len(df) < cfg.funding_zscore_period:
                self.log("WARN", f"funding rate history too short: {len(df)} rows")
                return 0.0
            z_series = calc_funding_zscore(df["fundingRate"], cfg.funding_zscore_period)
            z = float(z_series.iloc[-1])
            if not (z == z):  # NaN check / NaN检查
                return 0.0
            self.current_funding_z = z
            self._funding_cache = df["fundingRate"].tolist()
            self._funding_cache_ts = time.time()
            return z
        except Exception as e:
            self.log("WARN", f"funding rate fetch failed: {type(e).__name__}: {e}")
            return 0.0

    # ----- risk ----- / ----- 风控 -----

    def reset_daily(self) -> None:
        today = date.today()
        if today != self.last_date:
            self.last_date = today
            self.day_start_equity = self.starting_equity
            self.daily_pnl = 0.0
            if today.weekday() == 0:  # Monday / 周一
                self.week_start_equity = self.starting_equity
                self.weekly_pnl = 0.0
            self.log("INFO", "new day/week — daily/weekly counters reset")

    def can_open_new(self) -> tuple[bool, str]:
        cfg = self.cfg
        # Kill-switch / 熔断开关
        if KILLSWITCH_PATH.exists():
            try:
                reason = KILLSWITCH_PATH.read_text().strip()[:200]
            except OSError as e:
                self.log("WARN", f"killswitch file unreadable: {e}")
                reason = "(unknown)"
            return False, f"KILLSWITCH active: {reason}"
        # 24h cooldown after 3 consecutive losses / 连续3次亏损后24小时冷却
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
        # Post-trade bar cooldown / 交易后K线冷却
        if self._cooldown_until and time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            return False, f"post-trade cooldown: {remaining}s remaining ({cfg.cooldown_bars_after_trade} bars)"
        daily_loss = -self.daily_pnl
        if daily_loss >= cfg.daily_loss_pct * self.starting_equity:
            return False, f"daily loss cap hit: -${daily_loss:.4f} >= ${cfg.daily_loss_pct * self.starting_equity:.4f}"
        weekly_loss = -self.weekly_pnl
        if weekly_loss >= cfg.weekly_loss_pct * self.starting_equity:
            return False, f"weekly loss cap hit: -${weekly_loss:.4f} >= ${cfg.weekly_loss_pct * self.starting_equity:.4f}"
        # Permanent kill-switch at -10% cumulative (via SQLite trades.db). / 累计亏损-10%时永久熔断（通过SQLite trades.db）。
        # Excludes backfilled rows — those are historical/external fills / 排除回填行——那些是历史/外部成交
        # reconciled into the DB and must not count against the bot's PnL. / 仅用于对账不应计入机器人盈亏
        try:
            cum_pnl = self.store.cumulative_pnl()
            if cum_pnl <= -0.10 * self.starting_equity:
                KILLSWITCH_PATH.write_text(
                    f"auto-killswitch: cumulative pnl {cum_pnl:.4f} <= -10% of {self.starting_equity}"
                )
                self.log("CRITICAL", f"AUTO KILL-SWITCH: cumulative loss {cum_pnl:.4f} hit -10% of starting equity (excl. backfilled)")
                return False, f"auto kill-switch triggered: cum_pnl={cum_pnl:.4f}"
        except (sqlite3.Error, OSError) as e:
            self.log("WARN", f"could not check cumulative pnl: {type(e).__name__}: {e}")
        if self.starting_equity <= 0:
            return False, "no equity"
        return True, "ok"

    def check_position_stop_loss(self) -> bool:
        if not self.position:
            return False
        if self.position.pct_change < -self.cfg.stop_loss_pct:
            self.log("WARNING", f"STOP-LOSS hit: {self.symbol} {self.position.side} "
                     f"change={self.position.pct_change*100:.3f}% < -{self.cfg.stop_loss_pct*100:.2f}%; "
                     f"uPnl={self.position.u_pnl:.4f}")
            return True
        return False

    def check_position_take_profit(self) -> bool:
        if not self.position:
            return False
        if self.position.pct_change >= self.cfg.take_profit_pct:
            self.log("INFO", f"TAKE-PROFIT hit: {self.symbol} {self.position.side} "
                     f"change={self.position.pct_change*100:.3f}% >= +{self.cfg.take_profit_pct*100:.2f}%; "
                     f"uPnl={self.position.u_pnl:.4f}")
            return True
        return False

    # ----- actions ----- / ----- 操作 -----

    def _open_position(self, side: str, qty: float, reason: str) -> None:
        """Open a position. side='BUY' for long, 'SELL' for short."""
        pos_side = "LONG" if side == "BUY" else "SHORT"
        qty = round(qty, 3)
        if qty <= 0:
            return
        self.ex.cancel_all_orders()  # clean any leftover conditional orders / 清除残留的条件订单
        self.log("ACTION", f"OPEN {pos_side} {self.symbol} qty={qty} reason={reason}")
        r = self.ex.market_order(side, qty)
        self.log("ACTION", f"order response: {r}")
        if isinstance(r, dict) and "error" in r:
            self.log("ERROR", f"order FAILED — checking for orphaned fill: {r}")
            self.last_action = f"open FAILED reason={reason}"
            # Network timeout may have filled the order — verify on exchange
            # 网络超时可能已成交——在交易所端验证
            actual = self.ex.get_position()
            if actual:
                self.log("CRITICAL", f"orphaned position after failed order: {actual.side} qty={actual.qty}")
                self.position = actual
                stops_ok = self.ex.place_exchange_stops(
                    actual.side, actual.entry, self.cfg.stop_loss_pct,
                    self.cfg.take_profit_pct, self.cfg.price_tick,
                )
                if not stops_ok:
                    self.log("CRITICAL", "stops also failed on orphaned position — closing for safety")
                    self.close_position("safety_close_orphaned")
            return
        # Order succeeded — place exchange-side SL/TP
        # 订单成功——挂交易所端止损止盈
        entry = float(r.get("avgPrice", 0)) or 0
        if not entry:
            # Market order — fetch entry from position / 市价单——从持仓获取入场价
            pos = self.ex.get_position()
            if pos:
                entry = pos.entry
        if entry:
            stops_ok = self.ex.place_exchange_stops(
                pos_side, entry, self.cfg.stop_loss_pct,
                self.cfg.take_profit_pct, self.cfg.price_tick,
            )
            if not stops_ok:
                self.log("CRITICAL", "SL/TP placement failed — closing position for safety")
                self.close_position("safety_close_stops_failed")
                return
        else:
            self.log("WARN", "could not determine entry price — stops not placed")
        self.last_action = f"open_{pos_side.lower()} qty={qty}"

    def open_long(self, qty: float, reason: str) -> None:
        self._open_position("BUY", qty, reason)

    def open_short(self, qty: float, reason: str) -> None:
        self._open_position("SELL", qty, reason)

    def close_position(self, reason: str) -> None:
        if not self.position:
            return
        p = self.position
        close_side = "SELL" if p.is_long else "BUY"
        # Cancel exchange-side conditional orders before closing / 平仓前取消交易所端条件订单
        self.ex.cancel_all_orders()
        self.log("ACTION", f"CLOSE {p.side} {self.symbol} qty={p.qty:.3f} reason={reason}")
        r = self.ex.market_order(close_side, p.qty, reduce_only=True)
        # Don't accumulate pnl or clear position if close failed / 平仓失败时不累计盈亏不清除持仓
        if isinstance(r, dict) and "error" in r:
            self.log("ERROR", f"close order FAILED (position kept): {r}")
            self.last_action = f"close FAILED reason={reason}"
            return
        self.log("ACTION", f"close response: {r}")
        # Fetch realized PnL from exchange income endpoint (accurate, includes fees). / 从交易所收入接口获取已实现盈亏（准确，含手续费）。
        pnl = self.ex.fetch_last_realized_pnl()
        if pnl == 0.0:
            pnl = p.u_pnl
            self.log("WARN", f"realized PnL from income=0, using uPnl={pnl:+.4f} as fallback")
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        try:
            self.store.log_trade(
                symbol=self.symbol,
                side=close_side,
                price=p.mark,
                qty=p.qty,
                source="paper" if self.dry_run else "live",
                strategy=self.cfg.strategy_name,
                pnl=pnl,
                order_id=str(r.get("orderId", "")) if isinstance(r, dict) else "",
            )
        except Exception as e:
            self.log("WARN", f"could not log trade to sqlite: {e}")
        # Streak detection: 3 consecutive losses → 24h cooldown / 连败检测：连续3次亏损→24小时冷却
        self._check_streak()
        self._set_cooldown()
        self.position = None
        self.last_action = f"close reason={reason}"
        self._save_pnl_state()

    def _set_cooldown(self) -> None:
        """Start post-trade bar cooldown if configured. / 如已配置，启动交易后K线冷却。"""
        cfg = self.cfg
        if cfg.cooldown_bars_after_trade > 0:
            secs = cfg.cooldown_bars_after_trade * _interval_seconds(cfg.kline_interval)
            self._cooldown_until = time.time() + secs

    def _check_streak(self) -> None:
        """Check for 3 consecutive losing trades → 24h cooldown.

        Also called from __init__ on startup so a restart does not silently
        drop an active cooldown — the COOLDOWN_UNTIL file can be lost if the
        data dir is cleaned, the process is killed mid-write, or a prior ops
        session removed it.

        Guards:
          - If the cooldown file already exists and is still active, return
            immediately (don't reset the countdown on every restart).
          - If the most recent loss is >24h old, the cooldown has already
            been served — don't re-trigger a stale streak on restart.
        """
        try:
            # Don't reset an already-active cooldown / 不要重置已激活的冷却
            if COOLDOWN_PATH.exists():
                try:
                    if time.time() < float(COOLDOWN_PATH.read_text().strip()):
                        return  # cooldown already active — leave it alone / 冷却已激活——不要动它
                except (OSError, ValueError):
                    pass  # corrupt/unreadable file — fall through and re-evaluate / 文件损坏——继续重新评估
            recent = self.store.recent_trade_pnls(source="live", limit=3)
            if len(recent) == 3 and all(pnl < 0 for pnl, _ in recent):
                # Skip stale streaks: if the most recent loss is >24h old the / 跳过过期连败：最近亏损距今超24小时则
                # cooldown has already been served — don't re-trigger on restart. / 冷却已服满——重启时不重新触发。
                try:
                    last_ts = datetime.fromisoformat(recent[0][1].replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - last_ts).total_seconds() > 86400:
                        return
                except Exception:
                    pass  # can't parse ts — be conservative and trigger cooldown / 无法解析时间戳——保守触发冷却
                until = time.time() + 86400
                COOLDOWN_PATH.write_text(str(until))
                self.log("CRITICAL", f"3 CONSECUTIVE LOSSES — 24h COOLDOWN until {time.strftime('%F %T', time.localtime(until))}")
        except (sqlite3.Error, OSError, ValueError) as e:
            self.log("WARN", f"streak check failed: {type(e).__name__}: {e}")

    def _handle_external_close(self, prev_pos: Position) -> None:
        """Record a position closed by exchange-side SL/TP (not by the bot).

        The exchange's STOP_MARKET / TAKE_PROFIT_MARKET fires between polls,
        so the bot's next get_position() returns None.  Without this method
        the realized loss is invisible to daily/weekly caps, the -10% auto
        kill-switch, and streak detection — risk management runs blind.
        """
        close_side = "SELL" if prev_pos.is_long else "BUY"
        pnl = self.ex.fetch_last_realized_pnl()
        # Fallback: use cached uPnl if income API returned 0 / 兜底：income API返回0时用缓存uPnl
        if pnl == 0.0:
            pnl = prev_pos.u_pnl
            self.log("WARN", f"income API returned 0 — using cached uPnl={pnl:+.4f} as fallback")
        self.log("ACTION", f"EXTERNAL CLOSE {prev_pos.side} {self.symbol} qty={prev_pos.qty:.3f} "
                 f"entry={prev_pos.entry:.2f} realized_pnl={pnl:+.4f} (exchange SL/TP)")
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        try:
            self.store.log_trade(
                symbol=self.symbol, side=close_side, price=prev_pos.mark,
                qty=prev_pos.qty, source="live", strategy=self.cfg.strategy_name,
                pnl=pnl, order_id="exchange_sl_tp",
            )
        except Exception as e:
            self.log("WARN", f"could not log external close to sqlite: {e}")
        self._check_streak()
        self._set_cooldown()
        self._save_pnl_state()

    # ----- state dump ----- / ----- 状态转储 -----

    def dump_state(self) -> None:
        """Write current state to JSON so user can inspect on return."""
        state = {
            "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "tick": self.tick_count,
            "signal": self.last_signal,
            "starting_equity": self.starting_equity,
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "position": self.position.to_dict() if self.position else None,
            "dry_run": self.dry_run,
            "strategy": self.cfg.strategy_name,
            "constraints": {
                "leverage": self.cfg.leverage,
                "target_position_usdt": self.cfg.target_position_usdt,
                "stop_loss_pct": self.cfg.stop_loss_pct,
                "take_profit_pct": self.cfg.take_profit_pct,
                "daily_loss_pct": self.cfg.daily_loss_pct,
                "weekly_loss_pct": self.cfg.weekly_loss_pct,
            },
            "funding_z": self.current_funding_z,
        }
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            self.log("WARN", f"could not dump state: {e}")
        self._save_pnl_state()

    # ----- pnl persistence (survive restarts) ----- / ----- 盈亏持久化（跨重启存活）-----

    def _load_pnl_state(self) -> None:
        """Load daily/weekly pnl and cooldown from disk so risk caps survive restarts."""
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
            self._cooldown_until = float(s.get("cooldown_until", 0.0))
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
                    "cooldown_until": self._cooldown_until,
                }, f)
        except OSError as e:
            self.log("WARN", f"pnl_state save failed: {e}")

    # ----- main loop ----- / ----- 主循环 -----

    def tick(self) -> None:
        cfg = self.cfg
        self.tick_count += 1
        self.ex.maybe_resync_time()

        # IP ban backoff (HTTP 418 from Binance) / IP封禁退避（Binance返回HTTP 418）
        if self._banned_until and time.time() < self._banned_until:
            remaining = int(self._banned_until - time.time())
            if self.tick_count % 12 == 0:
                self.log("WARN", f"IP banned (418), backing off {remaining}s")
            return
        self._banned_until = 0.0

        if not self.dry_run and not self.starting_equity:
            self.fetch_account()
        self.reset_daily()

        # Fetch position EARLY so stop-loss works even if klines fail later / 尽早获取持仓，确保后续klines失败时止损仍生效
        if not self.dry_run:
            prev_position = self.position
            self.position = self.ex.get_position()
            # Detect exchange-side SL/TP closure (position vanished between polls). / 检测交易所端SL/TP平仓（持仓在轮询间消失）。
            if prev_position is not None and self.position is None:
                self._handle_external_close(prev_position)

        # In-memory SL/TP check (fallback for exchange-side stops) / 内存SL/TP检查（交易所端止损的兜底）
        if self.check_position_stop_loss():
            self.close_position("stop_loss")
            return

        if self.check_position_take_profit():
            self.close_position("take_profit")
            return

        df = self.ex.get_klines(interval=cfg.kline_interval, limit=300)
        if len(df) < cfg.warmup_bars:
            self.log("INFO", f"warmup — have {len(df)} bars, need {cfg.warmup_bars}")
            return

        bar = df.iloc[-1]
        sig = self.strategy.next_signal(bar, df)
        self.last_signal = sig.side.value

        risk_ok, why = self.can_open_new()
        ok = risk_ok  # ok will be further filtered by trend + funding / ok会被趋势+资金费率进一步过滤

        # Trend filter: skip new entries when market is trending strongly. / 趋势过滤：市场强趋势时跳过新开仓。
        # Mean-reversion (RSI extremes) gets chewed up in trends. / 均值回归（RSI极值）在趋势中被绞杀。
        trend_ok = True
        if cfg.trend_filter_enabled and not self.position:
            try:
                self.current_adx = float(calc_adx(df, period=14).iloc[-1])
                if self.current_adx > cfg.trend_filter_adx_threshold:
                    trend_ok = False
                    why = f"trending (ADX={self.current_adx:.1f} > {cfg.trend_filter_adx_threshold})"
            except Exception as e:
                self.log("WARN", f"ADX computation failed: {type(e).__name__}: {e}")

        # EMA trend-alignment filter — only long above EMA, only short below. / EMA趋势对齐过滤——只在EMA上方做多下方做空。
        if cfg.trend_ema_filter_enabled and not self.position:
            try:
                ema_series = calc_ema(df["close"], cfg.trend_ema_period)
                self.current_ema = float(ema_series.iloc[-1])
                mark = float(bar["close"])
                if sig.side == Side.BUY and mark < self.current_ema:
                    trend_ok = False
                    why = f"bearish (price {mark:.0f} < EMA{cfg.trend_ema_period} {self.current_ema:.0f})"
                elif sig.side == Side.SELL and mark > self.current_ema:
                    trend_ok = False
                    why = f"bullish (price {mark:.0f} > EMA{cfg.trend_ema_period} {self.current_ema:.0f})"
            except Exception as e:
                self.log("WARN", f"EMA computation failed: {type(e).__name__}: {e}")

        ok = risk_ok and trend_ok

        # v9: Funding rate signal / 资金费率信号
        sig_side = sig.side
        if cfg.funding_rate_enabled:
            self.current_funding_z = self._get_funding_zscore()
            z = self.current_funding_z
            ext = cfg.funding_zscore_extreme
            if abs(z) >= ext and risk_ok:
                # Standalone: extreme funding overrides trend filter but not risk filter
                # / 独立信号：极端费率绕过趋势过滤但不绕过风控
                if z > ext:
                    sig_side = Side.SELL
                    ok = True
                    why = f"funding z={z:.2f}>{ext} (longs overcrowded, standalone)"
                elif z < -ext:
                    sig_side = Side.BUY
                    ok = True
                    why = f"funding z={z:.2f}<-{ext} (shorts overcrowded, standalone)"
            elif sig_side != Side.FLAT and ok:
                # Confluence: confirm or reject RSI signal / 共振：确认或拒绝RSI信号
                if sig_side == Side.BUY and z > 0:
                    sig_side = Side.FLAT
                    ok = False
                    why = f"RSI BUY but funding z={z:.2f}>0 (no confluence)"
                elif sig_side == Side.SELL and z < 0:
                    sig_side = Side.FLAT
                    ok = False
                    why = f"RSI SELL but funding z={z:.2f}<0 (no confluence)"

        if not ok and self.tick_count % 30 == 0:
            self.log("INFO", f"paused: {why}")

        # Signal exit (can be disabled so winners ride to TP) / 信号退出（可禁用让盈利单跑到止盈）
        if not cfg.disable_signal_exit:
            if self.position and self.position.is_long and sig_side == Side.SELL:
                self.close_position(f"signal_sell ({sig.reason})")
                return
            if self.position and not self.position.is_long and sig_side == Side.BUY:
                self.close_position(f"signal_buy ({sig.reason})")
                return

        # No position & signal BUY → open LONG / 无持仓&信号买入→开多
        if (not self.position) and sig_side == Side.BUY and ok:
            mark = float(bar["close"])
            qty = (cfg.target_position_usdt * cfg.leverage) / mark
            self.open_long(qty, f"signal_buy ({sig.reason})" + (f" funding_z={self.current_funding_z:.2f}" if cfg.funding_rate_enabled else ""))
            if not self.dry_run:
                self.position = self.ex.get_position()
            return
        # No position & signal SELL → open SHORT / 无持仓&信号卖出→开空
        if (not self.position) and sig_side == Side.SELL and ok:
            mark = float(bar["close"])
            qty = (cfg.target_position_usdt * cfg.leverage) / mark
            self.open_short(qty, f"signal_sell ({sig.reason})" + (f" funding_z={self.current_funding_z:.2f}" if cfg.funding_rate_enabled else ""))
            if not self.dry_run:
                self.position = self.ex.get_position()
            return

        # Heartbeat every 5 minutes / 每5分钟心跳
        if self.tick_count % 10 == 0:
            pos = self.position
            if pos is None:
                pos_str = "FLAT"
            else:
                pos_str = (
                    f"{pos.side} qty={pos.qty:.3f} "
                    f"entry={pos.entry:.2f} mark={pos.mark:.2f} "
                    f"uPnl={pos.u_pnl:+.4f}"
                )
            self.log(
                "INFO",
                f"heartbeat tick={self.tick_count} sig={sig_side.value} "
                f"pos={pos_str} "
                f"adx={self.current_adx:.1f} "
                f"fund_z={self.current_funding_z:.2f} "
                f"daily_pnl={self.daily_pnl:+.4f} weekly_pnl={self.weekly_pnl:+.4f} "
                f"can_open={ok}",
            )

        # Always dump state at end of tick / 每次tick结束转储状态
        self.dump_state()

    def run(self) -> None:
        self.ex.sync_time()
        if not self.dry_run:
            self.fetch_account()
        self.ex.set_leverage(self.cfg.leverage)
        self.log("INFO",
                 f"STARTED symbol={self.symbol} leverage={self.cfg.leverage}x "
                 f"target={self.cfg.target_position_usdt} USDT stop={self.cfg.stop_loss_pct*100:.2f}%/pos "
                 f"daily_cap={self.cfg.daily_loss_pct*100:.1f}% weekly_cap={self.cfg.weekly_loss_pct*100:.1f}% "
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
                    self.log("ERROR", "HTTP 418 (IP banned by Binance) — backing off 300s")
                else:
                    self.log("ERROR", f"tick failed: HTTPError {status}: {e}")
            except Exception as e:
                self.log("ERROR", f"tick failed: {type(e).__name__}: {e}")
            for _ in range(self.cfg.poll_seconds):
                if self._stop:
                    break
                time.sleep(1)

        # Graceful exit: close position (skip in dry-run) / 优雅退出：平仓（dry-run模式跳过）
        try:
            if not self.dry_run:
                self.position = self.ex.get_position()
                if self.position:
                    self.close_position("shutdown")
        except Exception as e:
            self.log("ERROR", f"shutdown close failed: {e}; please close manually in Binance UI")
        self.log("INFO", f"EXITED cleanly. daily_pnl={self.daily_pnl:+.4f} weekly_pnl={self.weekly_pnl:+.4f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="don't place real orders, just log signals")
    p.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env"))
    args = p.parse_args()

    load_env_file(args.env_file)

    try:
        ctx = RuntimeContext.from_env(dry_run=args.dry_run)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    cfg = TraderConfig.from_yaml()

    print(f"Mode : {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"Base : {ctx.base_url}")
    print(f"Env  : {args.env_file}")
    print(f"Key  : ...{ctx.api_key[-4:]}  (redacted)")
    print()

    if not args.dry_run:
        print("=" * 60)
        print("LIVE MODE — REAL MONEY AT RISK")
        print(f"  - Stop-loss -{cfg.stop_loss_pct*100:.1f}% / Take-profit +{cfg.take_profit_pct*100:.1f}% per trade")
        print(f"  - Daily cap  -{cfg.daily_loss_pct*100:.0f}% of starting equity")
        print(f"  - Weekly cap -{cfg.weekly_loss_pct*100:.0f}% of starting equity")
        print(f"  - Single position, {cfg.symbol} only, {cfg.target_position_usdt} USDT, {cfg.leverage}x leverage")
        print("  - To stop gracefully:  kill -TERM <pid>")
        print("=" * 60)
        print()

    trader = LiveTrader(ctx.api_key, ctx.api_secret, ctx.base_url, dry_run=ctx.dry_run)
    trader.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
