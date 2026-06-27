"""Contract test for trader/exchange.py — the Binance IO boundary.

Locks down: (a) which Binance endpoints are called, (b) which methods
exist on BinanceFutures, (c) which params each call sends.

⚠️ If this test fails after a "strategy change", you accidentally
touched the IO layer. Either revert that change, or — if the change
is intentional (e.g. Binance API migration) — update this test in the
SAME commit so reviewers see the contract change explicitly.

DOES NOT make real HTTP calls. Pure unit test, runs in <1s.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from trader.exchange import BinanceFutures


# ─── Public surface: methods that strategy code is allowed to call ────── / 公共接口：策略代码允许调用的方法
# Adding a method here means: "yes, strategies may use this". / 在此添加方法意味着："是的，策略可以使用此方法"。
# Removing or renaming one here is a breaking contract change. / 在此删除或重命名方法是破坏性契约变更。
PUBLIC_METHODS = {
    "sync_time",
    "maybe_resync_time",
    "fetch_account",
    "set_leverage",
    "get_klines",
    "get_position",
    "fetch_last_realized_pnl",
    "get_open_orders",
    "get_open_algo_orders",
    "get_account_balance",
    "market_order",
    "cancel_all_orders",
    "place_algo_stop",
    "place_exchange_stops",
}

# ─── Endpoint contract: every (method, path) BinanceFutures may hit ──── / 端点契约：BinanceFutures可调用的每个(方法, 路径)
# Adding/removing rows here = formal Binance API contract change. / 在此添加/删除行 = 正式的Binance API契约变更。
EXPECTED_ENDPOINTS = {
    ("GET",    "/fapi/v1/time"),            # sync_time (unsigned) / 同步时间（未签名）
    ("GET",    "/fapi/v1/klines"),          # get_klines (unsigned) / 获取K线（未签名）
    ("GET",    "/fapi/v2/account"),         # fetch_account / 获取账户
    ("POST",   "/fapi/v1/leverage"),        # set_leverage / 设置杠杆
    ("GET",    "/fapi/v2/positionRisk"),    # get_position / 获取仓位
    ("GET",    "/fapi/v1/income"),          # fetch_last_realized_pnl / 获取最近已实现盈亏
    ("GET",    "/fapi/v1/openOrders"),      # get_open_orders / 获取未成交订单
    ("GET",    "/fapi/v1/openAlgoOrders"),  # get_open_algo_orders  (since 2025-12-09) / 获取未成交算法订单（自2025-12-09起）
    ("POST",   "/fapi/v1/order"),           # market_order / 市价订单
    ("DELETE", "/fapi/v1/allOpenOrders"),   # cancel_all_orders / 撤销全部订单
    ("DELETE", "/fapi/v1/algoOpenOrders"),  # cancel_all_orders / 撤销全部订单
    ("POST",   "/fapi/v1/algoOrder"),       # place_algo_stop / place_exchange_stops / 放置算法止损 / 放置交易所止损止盈
}


@pytest.fixture
def ex():
    return BinanceFutures(
        api_key="k", api_secret="s",
        base_url="https://fapi.binance.com",
        symbol="BTCUSDT", dry_run=False,
        log=lambda *_a, **_k: None,
    )


def test_public_method_surface_is_locked():
    actual = {n for n, _ in inspect.getmembers(BinanceFutures, inspect.isfunction)
              if not n.startswith("_")}
    extras = actual - PUBLIC_METHODS
    missing = PUBLIC_METHODS - actual
    msg = []
    if extras:
        msg.append(f"NEW public methods (not in contract): {sorted(extras)}")
    if missing:
        msg.append(f"REMOVED public methods (was in contract): {sorted(missing)}")
    assert not msg, "BinanceFutures public surface changed:\n  " + "\n  ".join(msg)


def _capture_calls(ex):
    """Patch _call + module requests to capture (method, path) without HTTP."""
    calls: list[tuple[str, str]] = []

    def fake_call(method, path, params=None):
        calls.append((method, path))
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {} if path != "/fapi/v2/positionRisk" else []
        return m

    return calls, fake_call


def test_signed_endpoint_contract(ex):
    """Every signed call hits a path in EXPECTED_ENDPOINTS."""
    calls, fake_call = _capture_calls(ex)
    with patch.object(ex, "_call", side_effect=fake_call):
        # Exercise every signed method / 执行每个签名方法
        ex.fetch_account()
        ex.set_leverage(5)
        ex.get_position()
        ex.fetch_last_realized_pnl()
        ex.get_open_orders()
        ex.get_open_algo_orders()
        ex.market_order("SELL", 0.001)
        ex.cancel_all_orders()
        ex.place_algo_stop(side="BUY", order_type="STOP_MARKET",
                           trigger_price=63000.0, qty=0.002)

    unknown = [c for c in calls if c not in EXPECTED_ENDPOINTS]
    assert not unknown, (
        f"UNAPPROVED Binance endpoint(s) called: {unknown}\n"
        "Either update EXPECTED_ENDPOINTS (contract change) or remove the call."
    )


def test_algo_order_uses_correct_endpoint_and_params(ex):
    """Regression guard: SL/TP MUST go to /fapi/v1/algoOrder with triggerPrice."""
    captured = {}

    def fake_call(method, path, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["params"] = params
        m = MagicMock(); m.status_code = 200; m.json.return_value = {"algoId": 1}
        return m

    with patch.object(ex, "_call", side_effect=fake_call):
        ex.place_algo_stop(side="BUY", order_type="STOP_MARKET",
                           trigger_price=63000.0, qty=0.002)

    assert captured["method"] == "POST"
    assert captured["path"] == "/fapi/v1/algoOrder", (
        "SL/TP must use /fapi/v1/algoOrder since Binance 2025-12-09 "
        "migration — /fapi/v1/order returns -4120 for conditional types."
    )
    assert captured["params"]["algoType"] == "CONDITIONAL"
    assert "triggerPrice" in captured["params"], "must use triggerPrice (not stopPrice)"
    assert "stopPrice" not in captured["params"]


def test_market_order_is_reduce_only_when_requested(ex):
    captured = {}

    def fake_call(method, path, params=None):
        captured.update(params or {})
        m = MagicMock(); m.status_code = 200; m.json.return_value = {}
        return m

    with patch.object(ex, "_call", side_effect=fake_call):
        ex.market_order("BUY", 0.002, reduce_only=True)

    assert captured.get("reduceOnly") == "true"
    assert captured.get("type") == "MARKET"


def test_dry_run_blocks_state_changing_calls(ex):
    ex.dry_run = True
    calls = []
    with patch.object(ex, "_call", side_effect=lambda *a, **k: calls.append(a) or MagicMock()):
        # These four MUST NOT hit the wire in dry-run / 这四个方法在dry-run模式下绝不能发送网络请求
        ex.set_leverage(10)
        ex.market_order("SELL", 0.001)
        ex.cancel_all_orders()
        ex.place_algo_stop(side="BUY", order_type="STOP_MARKET",
                           trigger_price=63000.0, qty=0.001)
    assert calls == [], f"dry_run leaked HTTP calls: {calls}"
