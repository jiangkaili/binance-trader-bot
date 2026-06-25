"""Tests for trader/ package (Phase 2 refactor)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

os.environ.setdefault("BINANCE_API_KEY", "dummy_for_tests")
os.environ.setdefault("BINANCE_API_SECRET", "dummy_for_tests")


@pytest.fixture
def tmp_data(monkeypatch):
    """Redirect trader.paths.* to a temp directory so tests don't touch live data."""
    import trader.paths as P
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(P, "DATA_DIR", tmp)
    monkeypatch.setattr(P, "TRADES_DB_PATH", tmp / "trades.db")
    monkeypatch.setattr(P, "LOG_PATH", tmp / "live.log")
    monkeypatch.setattr(P, "STATE_PATH", tmp / "state.json")
    monkeypatch.setattr(P, "PNL_STATE_PATH", tmp / "pnl.json")
    monkeypatch.setattr(P, "KILLSWITCH_PATH", tmp / "KILL")
    monkeypatch.setattr(P, "COOLDOWN_PATH", tmp / "COOL")
    # Reload modules that captured paths at import time
    import importlib
    import trader.risk
    import trader.state
    importlib.reload(trader.risk)
    importlib.reload(trader.state)
    yield tmp


# ---------- Position dataclass ----------

def test_position_long_pct_change():
    from trader.models import Position
    p = Position(side="LONG", qty=0.002, entry=100.0, mark=102.0, u_pnl=0.4, leverage="5")
    assert p.is_long is True
    assert abs(p.pct_change - 0.02) < 1e-9


def test_position_short_pct_change():
    from trader.models import Position
    p = Position(side="SHORT", qty=0.002, entry=100.0, mark=98.0, u_pnl=0.4, leverage="5")
    assert p.is_long is False
    assert abs(p.pct_change - 0.02) < 1e-9


def test_position_roundtrip():
    from trader.models import Position
    d = {"side": "SHORT", "qty": 0.002, "entry": 62495.2, "mark": 62333.49, "uPnl": 0.32, "leverage": "5"}
    p = Position.from_dict(d)
    assert p.to_dict() == d


# ---------- Config ----------

def test_config_defaults_match_legacy():
    """Built-in defaults must match the hardcoded values in scripts/live_trader.py."""
    from trader.config import TraderConfig
    cfg = TraderConfig()  # no yaml — pure defaults
    assert cfg.symbol == "BTCUSDT"
    assert cfg.leverage == 20
    assert cfg.target_position_usdt == 25.0
    assert cfg.stop_loss_pct == 0.01
    assert cfg.daily_loss_pct == 0.25
    assert cfg.weekly_loss_pct == 0.40
    assert cfg.kline_interval == "5m"
    assert cfg.poll_seconds == 60


def test_config_loads_yaml():
    """Loads from the actual config/trader.yaml shipped in repo."""
    from trader.config import TraderConfig
    cfg = TraderConfig.from_yaml()
    # config/trader.yaml is the live config; v5 uses 10x, stricter RSI and disables signal exit.
    assert cfg.leverage == 10
    assert cfg.rsi_oversold == 12.0
    assert cfg.rsi_overbought == 88.0
    assert cfg.stop_loss_pct == 0.005
    assert cfg.take_profit_pct == 0.010
    assert cfg.cooldown_bars_after_trade == 12
    assert cfg.disable_signal_exit is True


# ---------- Risk gates ----------

def test_risk_gate_clean_passes(tmp_data):
    from trader.config import TraderConfig
    from trader.risk import RiskManager, RiskState
    state = RiskState(starting_equity=40.0)
    rm = RiskManager(TraderConfig(), state, log=lambda *_: None)
    ok, why = rm.can_open_new()
    assert ok, why


def test_risk_gate_killswitch_blocks(tmp_data):
    from trader.config import TraderConfig
    import trader.risk as rmod
    state = rmod.RiskState(starting_equity=40.0)
    rmod.KILLSWITCH_PATH.write_text("manual emergency stop")
    rm = rmod.RiskManager(TraderConfig(), state, log=lambda *_: None)
    ok, why = rm.can_open_new()
    assert not ok
    assert "KILLSWITCH" in why


def test_risk_gate_daily_cap_blocks(tmp_data):
    from trader.config import TraderConfig
    from trader.risk import RiskManager, RiskState
    state = RiskState(starting_equity=40.0, daily_pnl=-20.0)  # 50% loss > 25% cap
    rm = RiskManager(TraderConfig(), state, log=lambda *_: None)
    ok, why = rm.can_open_new()
    assert not ok
    assert "daily loss cap" in why


def test_risk_gate_weekly_cap_blocks(tmp_data):
    from trader.config import TraderConfig
    from trader.risk import RiskManager, RiskState
    state = RiskState(starting_equity=40.0, weekly_pnl=-20.0)  # > 40% weekly cap
    rm = RiskManager(TraderConfig(), state, log=lambda *_: None)
    ok, why = rm.can_open_new()
    assert not ok
    assert "weekly loss cap" in why


def test_risk_hit_stop_loss_long(tmp_data):
    from trader.config import TraderConfig
    from trader.models import Position
    from trader.risk import RiskManager, RiskState
    rm = RiskManager(TraderConfig(stop_loss_pct=0.01), RiskState(starting_equity=40.0), log=lambda *_: None)
    losing = Position("LONG", 0.002, 100.0, 98.5, -0.003, "5")  # -1.5%
    assert rm.hit_stop_loss(losing) is True
    safe = Position("LONG", 0.002, 100.0, 99.5, -0.001, "5")    # -0.5%
    assert rm.hit_stop_loss(safe) is False


def test_risk_hit_take_profit_short(tmp_data):
    from trader.config import TraderConfig
    from trader.models import Position
    from trader.risk import RiskManager, RiskState
    rm = RiskManager(TraderConfig(take_profit_pct=0.015), RiskState(starting_equity=40.0), log=lambda *_: None)
    winner = Position("SHORT", 0.002, 100.0, 98.0, 0.004, "5")  # +2%
    assert rm.hit_take_profit(winner) is True
    holding = Position("SHORT", 0.002, 100.0, 99.0, 0.002, "5")  # +1%
    assert rm.hit_take_profit(holding) is False


# ---------- Cooldown -----

def test_cooldown_blocks_then_clears(tmp_data):
    import time
    from trader.config import TraderConfig
    import trader.risk as rmod
    state = rmod.RiskState(starting_equity=40.0)
    rmod.COOLDOWN_PATH.write_text(str(time.time() + 3600))  # 1h from now
    rm = rmod.RiskManager(TraderConfig(), state, log=lambda *_: None)
    ok, why = rm.can_open_new()
    assert not ok
    assert "cooldown" in why
    # Past cooldown auto-clears
    rmod.COOLDOWN_PATH.write_text(str(time.time() - 10))
    ok, why = rm.can_open_new()
    assert ok
    assert not rmod.COOLDOWN_PATH.exists()
