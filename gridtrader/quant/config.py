"""Configuration loader.

Reads from three sources, in order of precedence:
1. Environment variables (and .env file via python-dotenv)
2. YAML config file
3. Built-in defaults

This keeps API keys out of source code and lets you switch between
testnet/mainnet via env without touching config files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

# Load .env if present (does nothing if missing)
load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class ApiSettings:
    api_key: str = ""
    private_key: str = ""  # Ed25519 private key (Binance's new auth)
    secret: str = ""  # legacy HMAC secret (still supported on some endpoints)
    proxy_host: str = ""
    proxy_port: int = 0
    use_testnet: bool = False

    @classmethod
    def from_env(cls) -> "ApiSettings":
        return cls(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            private_key=os.getenv("BINANCE_PRIVATE_KEY", ""),
            secret=os.getenv("BINANCE_API_SECRET", ""),
            proxy_host=os.getenv("PROXY_HOST", ""),
            proxy_port=_env_int("PROXY_PORT", 0),
            use_testnet=_env_bool("USE_TESTNET", False),
        )


@dataclass
class RiskSettings:
    max_position_pct: float = 0.30  # max % of account per symbol
    max_daily_loss_pct: float = 0.05  # halt trading after 5% daily loss
    max_open_orders: int = 10
    max_single_order_value_pct: float = 0.10  # cap single order at 10% of account

    @classmethod
    def from_env(cls) -> "RiskSettings":
        return cls(
            max_position_pct=_env_float("RISK_MAX_POSITION_PCT", 0.30),
            max_daily_loss_pct=_env_float("RISK_MAX_DAILY_LOSS_PCT", 0.05),
            max_open_orders=_env_int("RISK_MAX_OPEN_ORDERS", 10),
            max_single_order_value_pct=_env_float("RISK_MAX_SINGLE_ORDER_VALUE_PCT", 0.10),
        )


@dataclass
class StorageSettings:
    db_path: str = "./data/trades.db"
    cache_dir: str = "./data/cache"

    @classmethod
    def from_env(cls) -> "StorageSettings":
        return cls(
            db_path=os.getenv("DB_PATH", "./data/trades.db"),
            cache_dir=os.getenv("CACHE_DIR", "./data/cache"),
        )


@dataclass
class QuantSettings:
    api: ApiSettings = field(default_factory=ApiSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    default_symbol: str = "BTCUSDT"
    log_level: str = "INFO"

    @classmethod
    def load(cls, yaml_path: Optional[str] = None) -> "QuantSettings":
        """Load settings from env (and optional YAML overlay)."""
        s = cls()
        s.api = ApiSettings.from_env()
        s.risk = RiskSettings.from_env()
        s.storage = StorageSettings.from_env()
        s.default_symbol = os.getenv("DEFAULT_SYMBOL", "BTCUSDT")
        s.log_level = os.getenv("LOG_LEVEL", "INFO")

        if yaml_path and Path(yaml_path).exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            for section, vals in raw.items():
                target = getattr(s, section, None)
                if target is None or not isinstance(vals, dict):
                    continue
                for k, v in vals.items():
                    if hasattr(target, k):
                        setattr(target, k, v)
        return s

    def validate(self, require_api: bool = True) -> list[str]:
        """Return list of validation errors (empty == OK)."""
        errs: list[str] = []
        if require_api:
            if not self.api.api_key:
                errs.append("BINANCE_API_KEY is empty (set in .env or env var)")
            if not self.api.private_key and not self.api.secret:
                errs.append("Need either BINANCE_PRIVATE_KEY (Ed25519) or BINANCE_API_SECRET (HMAC)")
        if not (0 < self.risk.max_position_pct <= 1):
            errs.append("risk.max_position_pct must be in (0, 1]")
        if not (0 < self.risk.max_daily_loss_pct <= 1):
            errs.append("risk.max_daily_loss_pct must be in (0, 1]")
        if self.risk.max_open_orders < 1:
            errs.append("risk.max_open_orders must be >= 1")
        return errs

    def to_dict(self) -> dict:
        return {
            "api": asdict(self.api),
            "risk": asdict(self.risk),
            "storage": asdict(self.storage),
            "default_symbol": self.default_symbol,
            "log_level": self.log_level,
        }
