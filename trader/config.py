"""Typed configuration loaded from config/trader.yaml.

A single TraderConfig dataclass replaces 18 module-level globals.
All defaults match the historical hardcoded values in
scripts/live_trader.py so behavior is preserved exactly.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import CONFIG_PATH


HOSTS = {
    "testnet": "https://testnet.binancefuture.com",
    "prod":    "https://fapi.binance.com",
}


@dataclass
class TraderConfig:
    # market
    symbol: str = "BTCUSDT"
    target_position_usdt: float = 25.0
    leverage: int = 20

    # strategy
    strategy_name: str = "rsi_extremes_5m"
    rsi_period: int = 7
    rsi_oversold: float = 20.0
    rsi_overbought: float = 80.0

    # risk
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.01
    daily_loss_pct: float = 0.25
    weekly_loss_pct: float = 0.40
    disable_signal_exit: bool = False

    # timing
    kline_interval: str = "5m"
    poll_seconds: int = 60
    warmup_bars: int = 50

    # tick-size for STOP_MARKET/TAKE_PROFIT_MARKET price rounding.
    # 0.1 is correct for BTCUSDT; other symbols need overrides via YAML.
    price_tick: float = 0.1

    @classmethod
    def from_yaml(cls, path: Path = CONFIG_PATH) -> "TraderConfig":
        if not path.exists():
            print(f"WARNING: {path} not found — using built-in defaults", file=sys.stderr)
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        # only keep keys we know about; ignore extras (forward-compatible YAML)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in raw.items() if k in known}
        return cls(**clean)


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
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
