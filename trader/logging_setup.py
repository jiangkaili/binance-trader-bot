"""Lightweight logger with file + stdout + SQLite triple-write.

Equivalent to LiveTrader.log() in v1.  Pulled into a free function so
risk/exchange/state modules can share it without inheriting from Trader.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def make_logger(log_path: Path, store_event: Callable[[str, str], None] | None = None) -> Callable[[str, str], None]:
    """Build a logger callable: log(level, msg).

    - prints to stdout
    - appends to log_path (creates parent dirs)
    - if store_event is given, also writes to SQLite via that callable

    All writes are best-effort; logging never crashes the loop.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(level: str, msg: str) -> None:
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        line = f"[{ts}] [{level}] {msg}"
        print(line, flush=True)
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            print(f"[log file write failed: {e}]", file=sys.stderr, flush=True)
        if store_event is not None:
            try:
                store_event(level, msg)
            except Exception as e:  # noqa: BLE001 — logging never crashes loop
                print(f"[log DB write failed: {type(e).__name__}: {e}]", file=sys.stderr, flush=True)

    return log
