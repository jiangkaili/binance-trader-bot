"""Tests for trader.config — YAML loading, runtime env, and .env parsing.

Covers:
  - TraderConfig.from_yaml(): loads custom values, ignores unknown keys,
    falls back to defaults when the file is missing, rejects bad cooldown.
  - RuntimeContext.from_env(): reads credentials + testnet flag from env,
    raises when credentials are absent.
  - load_env_file(): parses KEY=value lines (comments, quotes), and never
    overwrites vars that already exist in os.environ (setdefault semantics).
"""
# trader.config 测试 — YAML加载、运行时环境、.env解析。
# 覆盖: from_yaml读取自定义值并忽略未知键、文件缺失时回退默认值、拒绝非法冷却值;
#       from_env读取凭证与测试网标志、缺凭证时报错;
#       load_env_file解析KEY=value(注释/引号)且不覆盖已存在的环境变量(setdefault语义)。
import os

import pytest

from trader.config import TraderConfig, RuntimeContext, load_env_file, HOSTS


# -------- TraderConfig.from_yaml -------- / -------- from_yaml --------

def test_trader_config_from_yaml_loads_values(tmp_path):
    # custom values + an unknown key that must be ignored (forward-compat) / 自定义值 + 必须被忽略的未知键(前向兼容)
    yml = tmp_path / "trader.yaml"
    yml.write_text(
        "symbol: ETHUSDT\n"
        "leverage: 10\n"
        "rsi_period: 14\n"
        "rsi_oversold: 25.0\n"
        "rsi_overbought: 75.0\n"
        "unknown_future_key: should_be_ignored\n",
        encoding="utf-8",
    )
    cfg = TraderConfig.from_yaml(yml)
    # overridden values / 被覆盖的值
    assert cfg.symbol == "ETHUSDT"
    assert cfg.leverage == 10
    assert cfg.rsi_period == 14
    assert cfg.rsi_oversold == 25.0
    assert cfg.rsi_overbought == 75.0
    # un-set fields keep their built-in defaults / 未设置字段保留内置默认值
    assert cfg.stop_loss_pct == 0.01
    assert cfg.take_profit_pct == 0.01


def test_trader_config_defaults_when_file_missing(tmp_path):
    # non-existent path → built-in defaults (no crash) / 不存在路径 → 内置默认值(不报错)
    cfg = TraderConfig.from_yaml(tmp_path / "does_not_exist.yaml")
    assert cfg.symbol == "BTCUSDT"
    assert cfg.leverage == 20
    assert cfg.rsi_period == 7


def test_trader_config_rejects_negative_cooldown(tmp_path):
    # negative cooldown is invalid and must raise / 负冷却值非法, 必须报错
    yml = tmp_path / "trader.yaml"
    yml.write_text("cooldown_bars_after_trade: -1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        TraderConfig.from_yaml(yml)


# -------- RuntimeContext.from_env -------- / -------- from_env --------

def test_runtime_context_testnet(monkeypatch):
    # testnet mode → testnet host, dry_run passed through / 测试网模式 → 测试网主机, dry_run透传
    monkeypatch.setenv("BINANCE_API_KEY", "mykey")
    monkeypatch.setenv("BINANCE_API_SECRET", "mysecret")
    monkeypatch.setenv("USE_TESTNET", "true")
    ctx = RuntimeContext.from_env(dry_run=True)
    assert ctx.api_key == "mykey"
    assert ctx.api_secret == "mysecret"
    assert ctx.use_testnet is True
    assert ctx.dry_run is True
    assert ctx.base_url == HOSTS["testnet"]


def test_runtime_context_prod(monkeypatch):
    # USE_TESTNET=false → prod host / USE_TESTNET=false → 生产主机
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    monkeypatch.setenv("USE_TESTNET", "false")
    ctx = RuntimeContext.from_env(dry_run=False)
    assert ctx.use_testnet is False
    assert ctx.dry_run is False
    assert ctx.base_url == HOSTS["prod"]


def test_runtime_context_missing_creds_raises(monkeypatch):
    # no credentials → RuntimeError / 无凭证 → RuntimeError
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        RuntimeContext.from_env(dry_run=True)


# -------- load_env_file -------- / -------- load_env_file --------

# unique test-only keys so we never collide with real env / 唯一的测试专用键, 不会与真实环境冲突
_KEYS = ("TESTCFG_KEY1", "TESTCFG_KEY2", "TESTCFG_KEY3", "TESTCFG_NEW")


def test_load_env_file_parses_lines(tmp_path, monkeypatch):
    # comments, blank lines, quoted values all handled / 注释、空行、引号值均能处理
    env = tmp_path / ".env"
    env.write_text(
        "# a comment / 注释\n"
        "TESTCFG_KEY1=value1\n"
        'TESTCFG_KEY2="quoted value"\n'
        "TESTCFG_KEY3='single quoted'\n"
        "\n"
        "TESTCFG_NEW=fresh\n",
        encoding="utf-8",
    )
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    try:
        load_env_file(str(env))
        assert os.environ["TESTCFG_KEY1"] == "value1"
        assert os.environ["TESTCFG_KEY2"] == "quoted value"      # quotes stripped / 去引号
        assert os.environ["TESTCFG_KEY3"] == "single quoted"
        assert os.environ["TESTCFG_NEW"] == "fresh"
    finally:
        # clean up keys injected via setdefault (not tracked by monkeypatch) / 清理setdefault注入的键(monkeypatch未跟踪)
        for k in _KEYS:
            os.environ.pop(k, None)


def test_load_env_file_does_not_overwrite_existing(tmp_path, monkeypatch):
    # setdefault: a pre-existing var keeps its original value / setdefault: 已存在的变量保留原值
    monkeypatch.setenv("TESTCFG_EXISTING", "original")
    env = tmp_path / ".env"
    env.write_text("TESTCFG_EXISTING=should_not_apply\n", encoding="utf-8")
    load_env_file(str(env))
    assert os.environ["TESTCFG_EXISTING"] == "original"
    # monkeypatch restores TESTCFG_EXISTING at teardown / monkeypatch在teardown时恢复原值


def test_load_env_file_missing_path_is_noop(tmp_path):
    # missing file → silent no-op / 文件缺失 → 静默无操作
    assert load_env_file(str(tmp_path / "nope.env")) is None
