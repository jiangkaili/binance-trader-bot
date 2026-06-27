"""Tests for gridtrader.quant.storage — SQLite trade/event persistence.

Covers:
  - log_trade() + trades() round-trip (fields survive a write+read).
  - log_event() persists an event row (verified by direct read-back).
  - trades() filtering by symbol, source, and since (timestamp cutoff),
    using a monkeypatched clock for deterministic timestamps.
"""
# gridtrader.quant.storage 测试 — SQLite交易/事件持久化。
# 覆盖: log_trade()+trades()往返(字段经写入读取后保持一致);
#       log_event()持久化事件行(直接读回验证);
#       trades()按symbol、source、since(时间戳截止)过滤, 使用monkeypatch时钟保证时间戳确定。
import sqlite3

import gridtrader.quant.storage as storage_mod
from gridtrader.quant.storage import Store


def _open_store(tmp_path):
    # fresh isolated SQLite DB per test / 每个测试使用独立的新SQLite数据库
    return Store(str(tmp_path / "trades.db"))


# -------- round-trip -------- / -------- 往返 --------

def test_log_trade_roundtrip(tmp_path):
    store = _open_store(tmp_path)
    trade_id = store.log_trade(
        symbol="BTCUSDT", side="BUY", price=50_000.0, qty=0.01,
        source="paper", fee=1.5, strategy="rsi_revert",
    )
    assert trade_id > 0  # got a real id / 拿到真实id

    df = store.trades()
    assert len(df) == 1
    row = df.iloc[0]
    # every field round-trips intact / 每个字段往返保持完整
    assert row["symbol"] == "BTCUSDT"
    assert row["side"] == "BUY"
    assert row["price"] == 50_000.0
    assert row["qty"] == 0.01
    assert row["source"] == "paper"
    assert row["fee"] == 1.5
    assert row["strategy"] == "rsi_revert"


def test_multiple_trades_ordered(tmp_path):
    # several trades come back ordered by timestamp / 多笔交易按时间戳排序返回
    store = _open_store(tmp_path)
    store.log_trade(symbol="BTCUSDT", side="BUY", price=100, qty=1, source="paper")
    store.log_trade(symbol="BTCUSDT", side="SELL", price=101, qty=1, source="paper")
    df = store.trades()
    assert len(df) == 2
    assert list(df["side"]) == ["BUY", "SELL"]  # insertion order = ts order / 插入顺序即时间戳顺序


# -------- log_event -------- / -------- log_event --------

def test_log_event_persists(tmp_path):
    db = str(tmp_path / "trades.db")
    store = Store(db)
    eid = store.log_event(level="INFO", msg="strategy started", strategy="rsi_revert")
    assert eid > 0

    # read back the row directly (Store has no public events() reader) / 直接读回该行(Store无公开events()读取方法)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    con.close()
    assert row["level"] == "INFO"
    assert row["msg"] == "strategy started"
    assert row["strategy"] == "rsi_revert"


# -------- filtering -------- / -------- 过滤 --------

def test_filter_by_symbol_and_source(tmp_path):
    store = _open_store(tmp_path)
    store.log_trade(symbol="BTCUSDT", side="BUY",  price=50_000, qty=0.1, source="paper")
    store.log_trade(symbol="ETHUSDT", side="SELL", price=3_000,  qty=1.0, source="live")
    store.log_trade(symbol="BTCUSDT", side="SELL", price=51_000, qty=0.1, source="live")

    # by symbol / 按交易对
    df_btc = store.trades(symbol="BTCUSDT")
    assert len(df_btc) == 2
    assert set(df_btc["symbol"]) == {"BTCUSDT"}

    # by source / 按来源
    df_live = store.trades(source="live")
    assert len(df_live) == 2
    assert set(df_live["source"]) == {"live"}

    # combined symbol + source / 组合 symbol + source
    df_both = store.trades(symbol="BTCUSDT", source="live")
    assert len(df_both) == 1
    assert df_both.iloc[0]["side"] == "SELL"


def test_filter_by_since(tmp_path, monkeypatch):
    # deterministic timestamps via a patched clock (no real-time sleeps) / 通过打补丁的时钟获得确定性时间戳(无实时sleep)
    ts_seq = iter([
        "2024-01-01T00:00:00.000+00:00",
        "2024-01-02T00:00:00.000+00:00",
        "2024-01-03T00:00:00.000+00:00",
    ])
    monkeypatch.setattr(storage_mod, "_now_iso", lambda: next(ts_seq))

    store = _open_store(tmp_path)
    store.log_trade(symbol="BTCUSDT", side="BUY", price=50_000, qty=0.1, source="paper")
    store.log_trade(symbol="ETHUSDT", side="BUY", price=3_000,  qty=1.0, source="paper")
    store.log_trade(symbol="SOLUSDT", side="BUY", price=100,    qty=10.0, source="paper")

    # sanity: all three persisted / 完整性: 三笔均已持久化
    assert len(store.trades()) == 3

    # since = 2nd ts → only 2nd and 3rd (ts >= since) / since=第2个时间戳 → 仅第2、3笔
    df = store.trades(since="2024-01-02T00:00:00.000+00:00")
    assert len(df) == 2
    assert set(df["symbol"]) == {"ETHUSDT", "SOLUSDT"}

    # since before all → all three / since早于全部 → 三笔全返回
    df_all = store.trades(since="2024-01-01T00:00:00.000+00:00")
    assert len(df_all) == 3
