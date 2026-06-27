"""Typed configuration loaded from config/trader.yaml.

A single TraderConfig dataclass replaces 18 module-level globals.
All defaults match the historical hardcoded values in
scripts/live_trader.py so behavior is preserved exactly.
"""
# 从config/trader.yaml加载的类型化配置。单个TraderConfig数据类替代18个模块级全局变量，
# 所有默认值与scripts/live_trader.py中的历史硬编码值一致，确保行为完全保留。
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from .paths import CONFIG_PATH


HOSTS = {
    "testnet": "https://testnet.binancefuture.com",
    "prod":    "https://fapi.binance.com",
}


@dataclass
class TraderConfig:
    # market / 市场
    symbol: str = "BTCUSDT"
    target_position_usdt: float = 25.0
    leverage: int = 20

    # strategy / 策略
    strategy_name: str = "rsi_extremes_5m"
    rsi_period: int = 7
    rsi_oversold: float = 20.0
    rsi_overbought: float = 80.0

    # risk / 风险
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.01
    daily_loss_pct: float = 0.25
    weekly_loss_pct: float = 0.40
    disable_signal_exit: bool = False

    # timing / 时间
    kline_interval: str = "5m"
    poll_seconds: int = 60
    warmup_bars: int = 50

    # post-trade throttle: after any close, wait N completed 5m bars before
    # opening again. This reduces RSI-cluster overtrading after a stop/TP.
    # 交易后节流：平仓后等待N根已完成的5分钟K线再开仓。这减少了止损/止盈后RSI聚集导致的过度交易。
    cooldown_bars_after_trade: int = 0

    # tick-size for STOP_MARKET/TAKE_PROFIT_MARKET price rounding.
    # 0.1 is correct for BTCUSDT; other symbols need overrides via YAML.
    # STOP_MARKET/TAKE_PROFIT_MARKET价格取整的tick大小。0.1适用于BTCUSDT；其他交易对需通过YAML覆盖。
    price_tick: float = 0.1

    @classmethod
    def from_yaml(cls, path: Path = CONFIG_PATH) -> "TraderConfig":
        if not path.exists():
            print(f"WARNING: {path} not found — using built-in defaults", file=sys.stderr)
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # only keep keys we know about; ignore extras (forward-compatible YAML) / 仅保留已知键；忽略多余键（前向兼容YAML）
        known = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in raw.items() if k in known}
        cfg = cls(**clean)
        if cfg.cooldown_bars_after_trade < 0:
            raise ValueError("cooldown_bars_after_trade must be >= 0")
        return cfg


@dataclass
class RuntimeContext:
    """Per-process runtime config — credentials + mode."""
    api_key: str
    api_secret: str
    base_url: str
    use_testnet: bool
    dry_run: bool

    @classmethod
    def from_env(cls, dry_run: bool) -> "RuntimeContext":
        api_key = os.getenv("BINANCE_API_KEY", "").strip()
        api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
        use_testnet = os.getenv("USE_TESTNET", "true").strip().lower() in ("1", "true", "yes")
        base = HOSTS["testnet" if use_testnet else "prod"]
        if not api_key or not api_secret:
            raise RuntimeError("BINANCE_API_KEY / BINANCE_API_SECRET not set")
        return cls(
            api_key=api_key, api_secret=api_secret,
            base_url=base, use_testnet=use_testnet, dry_run=dry_run,
        )


def load_env_file(path: str) -> None:
    """Load .env-style file into os.environ.

    Lines like KEY=value, optional quotes, # comments.
    Existing env vars are NOT overwritten.
    """
    # 加载.env格式文件到os.environ。支持KEY=value格式、可选引号、#注释。已存在的环境变量不会被覆盖。
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
