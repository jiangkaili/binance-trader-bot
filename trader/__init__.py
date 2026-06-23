"""Hermes trader v2 — modular re-implementation of scripts/live_trader.py.

Use `python -m trader` (or `python -m trader --dry-run`) to run.

The legacy entry point at scripts/live_trader.py remains the
canonical production-running module until this package is validated
end-to-end against the live account. Behaviour MUST be identical.
"""
from __future__ import annotations

__version__ = "2.0.0-phase2"

from .trader import Trader  # noqa: E402,F401  re-export for convenience
