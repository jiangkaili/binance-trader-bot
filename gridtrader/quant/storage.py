"""SQLite-backed persistence for trades, orders, and strategy events.

Schema is intentionally simple — three tables:
  - trades:     every fill (paper or live)
  - orders:     every order request
  - events:     strategy log / state events (debugging + audit)

All writes go through context managers that commit on exit. Reads return
pandas DataFrames for ergonomic analysis.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                -- ISO 8601 UTC
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,              -- BUY / SELL
    price REAL NOT NULL,
    qty REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    fee_asset TEXT,
    strategy TEXT,
    order_id TEXT,
    source TEXT NOT NULL,            -- paper / live
    pnl REAL                         -- realized PnL for this fill (0 if opening)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,              -- MARKET / LIMIT
    price REAL,                      -- NULL for market
    qty REAL NOT NULL,
    status TEXT NOT NULL,            -- SUBMITTED / FILLED / CANCELLED / REJECTED
    strategy TEXT,
    order_id TEXT,
    source TEXT NOT NULL,
    extra TEXT                       -- JSON blob for strategy-specific data
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy TEXT,
    level TEXT NOT NULL,             -- INFO / WARNING / ERROR
    msg TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_ts ON trades(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_orders_symbol_ts ON orders(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_events_strategy_ts ON events(strategy, ts);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    """Thread-safe SQLite wrapper. Safe for the event engine's threads."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False because the event engine calls from worker threads.
        # The lock above serializes writes; reads use a fresh connection.
        self._init_conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_conn.executescript(_SCHEMA)
        self._init_conn.commit()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # -------- writes --------

    def log_trade(
        self,
        *,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        source: str,
        fee: float = 0.0,
        fee_asset: str = "",
        strategy: str = "",
        order_id: str = "",
        pnl: float = 0.0,
    ) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                """INSERT INTO trades
                   (ts, symbol, side, price, qty, fee, fee_asset, strategy, order_id, source, pnl)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_now_iso(), symbol, side, price, qty, fee, fee_asset, strategy, order_id, source, pnl),
            )
            return int(cur.lastrowid or 0)

    def log_order(
        self,
        *,
        symbol: str,
        side: str,
        type_: str,
        qty: float,
        status: str,
        source: str,
        price: Optional[float] = None,
        strategy: str = "",
        order_id: str = "",
        extra: Optional[dict] = None,
    ) -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                """INSERT INTO orders
                   (ts, symbol, side, type, price, qty, status, strategy, order_id, source, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _now_iso(), symbol, side, type_, price, qty, status,
                    strategy, order_id, source,
                    json.dumps(extra) if extra else None,
                ),
            )
            return int(cur.lastrowid or 0)

    def log_event(self, *, level: str, msg: str, strategy: str = "") -> int:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (ts, strategy, level, msg) VALUES (?, ?, ?, ?)",
                (_now_iso(), strategy, level, msg),
            )
            return int(cur.lastrowid or 0)

    # -------- reads --------

    def trades(
        self,
        symbol: Optional[str] = None,
        source: Optional[str] = None,
        since: Optional[str] = None,
    ) -> pd.DataFrame:
        q = "SELECT * FROM trades WHERE 1=1"
        params: list = []
        if symbol:
            q += " AND symbol = ?"
            params.append(symbol)
        if source:
            q += " AND source = ?"
            params.append(source)
        if since:
            q += " AND ts >= ?"
            params.append(since)
        q += " ORDER BY ts"
        with self._conn() as c:
            return pd.read_sql_query(q, c, params=params)

    def orders(self, symbol: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT * FROM orders WHERE 1=1"
        params: list = []
        if symbol:
            q += " AND symbol = ?"
            params.append(symbol)
        q += " ORDER BY ts"
        with self._conn() as c:
            return pd.read_sql_query(q, c, params=params)

    def events(self, strategy: Optional[str] = None, limit: int = 200) -> pd.DataFrame:
        q = "SELECT * FROM events"
        params: list = []
        if strategy:
            q += " WHERE strategy = ?"
            params.append(strategy)
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            return pd.read_sql_query(q, c, params=params)

    def daily_pnl(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Return daily PnL aggregated from trades."""
        df = self.trades(symbol=symbol)
        if df.empty:
            return pd.DataFrame(columns=["day", "pnl", "trades", "volume"])
        df["day"] = pd.to_datetime(df["ts"]).dt.date.astype(str)
        out = df.groupby("day").agg(
            pnl=("pnl", "sum"),
            trades=("id", "count"),
            volume=("qty", "sum"),
        ).reset_index()
        return out
