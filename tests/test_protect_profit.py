"""Tests for floating-profit protection (trader.protect).

These cover the decision rule only — no exchange, no clock. The rule is what
decides whether a real stop order gets replaced on Binance, so the boundary
cases matter: never move backwards, never place the stop on the wrong side of
the price, never act on a position that is not actually in profit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trader.protect import ProtectDecision, decide_protection, floating_pct


# --------------------------------------------------------------------------
# floating_pct
# --------------------------------------------------------------------------

def test_floating_pct_long():
    assert floating_pct(True, 100.0, 103.0) == pytest.approx(0.03)
    assert floating_pct(True, 100.0, 97.0) == pytest.approx(-0.03)


def test_floating_pct_short():
    assert floating_pct(False, 100.0, 97.0) == pytest.approx(0.03)
    assert floating_pct(False, 100.0, 103.0) == pytest.approx(-0.03)


@pytest.mark.parametrize("entry,mark", [(0.0, 100.0), (100.0, 0.0), (-1.0, 100.0)])
def test_floating_pct_bad_input(entry, mark):
    assert floating_pct(True, entry, mark) == 0.0


# --------------------------------------------------------------------------
# decide_protection — the gates
# --------------------------------------------------------------------------

def _long(**kw):
    base = dict(is_long=True, entry=100.0, mark=100.0, current_stop=98.5,
                trigger_pct=0.015, lock_pct=0.001, trail_pct=0.0)
    base.update(kw)
    return decide_protection(**base)


def _short(**kw):
    base = dict(is_long=False, entry=100.0, mark=100.0, current_stop=101.5,
                trigger_pct=0.015, lock_pct=0.001, trail_pct=0.0)
    base.update(kw)
    return decide_protection(**base)


def test_no_move_below_trigger():
    # +1.0% < 1.5% trigger
    assert _long(mark=101.0) is None


def test_moves_exactly_at_trigger():
    d = _long(mark=101.5)
    assert isinstance(d, ProtectDecision)
    assert d.reason == "breakeven"
    assert d.target == pytest.approx(100.1)


def test_long_breakeven_sits_above_entry():
    d = _long(mark=102.0)
    assert d.target > 100.0          # locked in profit
    assert d.target < 102.0          # still below the mark


def test_short_breakeven_sits_below_entry():
    d = _short(mark=98.0)
    assert d.target < 100.0          # locked in profit for a short
    assert d.target > 98.0


def test_never_moves_stop_backwards_long():
    # stop already at 100.5 (better than the 100.1 breakeven level)
    assert _long(mark=102.0, current_stop=100.5) is None


def test_never_moves_stop_backwards_short():
    assert _short(mark=98.0, current_stop=99.5) is None


def test_ignores_unknown_current_stop():
    d = _long(mark=102.0, current_stop=None)
    assert d is not None


def test_refuses_stop_on_wrong_side_long():
    # a "lock" wider than the profit would try to put the stop above price
    assert _long(mark=101.5, lock_pct=0.02) is None


def test_refuses_stop_on_wrong_side_short():
    assert _short(mark=98.5, lock_pct=0.02) is None


def test_zero_lock_places_stop_at_entry():
    d = _long(mark=102.0, lock_pct=0.0)
    assert d.target == pytest.approx(100.0)


# --------------------------------------------------------------------------
# trailing
# --------------------------------------------------------------------------

def test_trailing_off_by_default():
    d = _long(mark=105.0)
    assert d.reason == "breakeven"


def test_trailing_pulls_stop_behind_mark_long():
    d = _long(mark=105.0, trail_pct=0.01)
    assert d.reason == "trail"
    assert d.target == pytest.approx(105.0 * 0.99)


def test_trailing_pulls_stop_behind_mark_short():
    d = _short(mark=95.0, trail_pct=0.01)
    assert d.reason == "trail"
    assert d.target == pytest.approx(95.0 * 1.01)


def test_trailing_never_worse_than_breakeven_long():
    # tiny profit, trail would sit far below entry → breakeven wins
    d = _long(mark=101.6, trail_pct=0.05)
    assert d.reason == "breakeven"
    assert d.target == pytest.approx(100.1)


# --------------------------------------------------------------------------
# idempotence — calling it every tick must be safe
# --------------------------------------------------------------------------

def test_idempotent_after_move_long():
    first = _long(mark=103.0)
    assert first is not None
    second = _long(mark=103.0, current_stop=round(first.target, 1))
    assert second is None


def test_idempotent_after_move_short():
    first = _short(mark=97.0)
    assert first is not None
    second = _short(mark=97.0, current_stop=round(first.target, 1))
    assert second is None


# --------------------------------------------------------------------------
# sanity: real-world numbers from the live account
# --------------------------------------------------------------------------

def test_real_live_position_numbers():
    """The 2026-11-23 trade: SHORT 0.001 BTC entered at 62371, stopped at 63306.6.

    Had it instead reached +1.5% (i.e. 61435.5), protection should have offered
    a stop just below entry.
    """
    entry = 62371.00
    mark = entry * (1 - 0.015)
    d = decide_protection(is_long=False, entry=entry, mark=mark,
                          current_stop=entry * (1 + 0.015),
                          trigger_pct=0.015, lock_pct=0.001, trail_pct=0.0)
    assert d is not None
    assert d.target == pytest.approx(entry * (1 - 0.001))
    assert d.target < entry              # profit locked
    assert d.target > mark               # stop above current price
