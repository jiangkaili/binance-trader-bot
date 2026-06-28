"""Tests for funding rate signal (v9).

Tests the funding_zscore indicator and the confluence/standalone signal logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gridtrader.quant.indicators import funding_zscore


class TestFundingZscore:
    """Test the funding_zscore indicator."""

    def test_basic_zscore(self):
        """Z-score should be ~0 when value is close to mean."""
        rates = pd.Series([0.0001, 0.00012, 0.00009, 0.00011] * 8)
        z = funding_zscore(rates, period=30)
        assert z.iloc[-1] == pytest.approx(0.0, abs=0.5)

    def test_extreme_positive(self):
        """A spike should produce a high positive z-score."""
        rates = pd.Series([0.0001] * 30 + [0.001])  # 10x normal
        z = funding_zscore(rates, period=30)
        assert z.iloc[-1] > 2.0  # should be significantly positive

    def test_extreme_negative(self):
        """A negative spike should produce a low negative z-score."""
        rates = pd.Series([0.0001] * 30 + [-0.0005])
        z = funding_zscore(rates, period=30)
        assert z.iloc[-1] < -2.0

    def test_warmup_nan(self):
        """Z-score should be NaN during warmup (< period samples)."""
        rates = pd.Series([0.0001, 0.0002, 0.0003])
        z = funding_zscore(rates, period=30)
        assert pd.isna(z.iloc[-1])

    def test_period_too_small(self):
        """Period < 2 should raise ValueError."""
        rates = pd.Series([0.0001, 0.0002])
        with pytest.raises(ValueError, match="period must be >= 2"):
            funding_zscore(rates, period=1)

    def test_constant_series(self):
        """When all values are identical, std=0 → z=NaN (handled gracefully)."""
        rates = pd.Series([0.0001] * 31)
        z = funding_zscore(rates, period=30)
        # std=0 → division by zero → NaN or inf
        assert pd.isna(z.iloc[-1]) or np.isinf(z.iloc[-1]) or z.iloc[-1] == 0.0

    def test_realistic_funding_pattern(self):
        """Simulate realistic funding: mostly 0.01%, occasional spikes."""
        np.random.seed(42)
        normal = np.random.normal(0.0001, 0.00005, 30)
        rates = pd.Series(list(normal) + [0.0005])  # spike at end
        z = funding_zscore(rates, period=30)
        assert z.iloc[-1] > 2.0  # spike should be > 2 std devs
        assert z.iloc[-1] < 10.0  # but not absurdly high


class TestConfluenceLogic:
    """Test the confluence filter logic (unit test of the decision rules).

    These tests verify the signal logic without needing a live BinanceFutures.
    """

    @staticmethod
    def _apply_funding_rules(rsi_signal: str, z: float,
                             threshold: float = 2.0,
                             extreme: float = 3.0) -> str:
        """Replicate the funding rate signal logic from live_trader.tick().

        Returns the final signal: 'BUY', 'SELL', or 'FLAT'.
        """
        # Standalone: extreme funding overrides RSI
        if abs(z) >= extreme:
            if z > extreme:
                return "SELL"
            elif z < -extreme:
                return "BUY"

        # Confluence: reject if funding disagrees
        if rsi_signal == "BUY" and z > 0:
            return "FLAT"
        if rsi_signal == "SELL" and z < 0:
            return "FLAT"

        return rsi_signal

    def test_rsi_buy_funding_confirms(self):
        """RSI BUY + funding z < 0 (shorts paying) → BUY confirmed."""
        assert self._apply_funding_rules("BUY", -1.5) == "BUY"

    def test_rsi_buy_funding_rejects(self):
        """RSI BUY + funding z > 0 (longs paying) → FLAT (no confluence)."""
        assert self._apply_funding_rules("BUY", 0.5) == "FLAT"

    def test_rsi_sell_funding_confirms(self):
        """RSI SELL + funding z > 0 (longs overcrowded) → SELL confirmed."""
        assert self._apply_funding_rules("SELL", 1.5) == "SELL"

    def test_rsi_sell_funding_rejects(self):
        """RSI SELL + funding z < 0 (shorts overcrowded) → FLAT."""
        assert self._apply_funding_rules("SELL", -0.5) == "FLAT"

    def test_standalone_extreme_positive(self):
        """z > extreme → SELL regardless of RSI signal."""
        assert self._apply_funding_rules("FLAT", 3.5) == "SELL"
        assert self._apply_funding_rules("BUY", 3.5) == "SELL"

    def test_standalone_extreme_negative(self):
        """z < -extreme → BUY regardless of RSI signal."""
        assert self._apply_funding_rules("FLAT", -3.5) == "BUY"
        assert self._apply_funding_rules("SELL", -3.5) == "BUY"

    def test_rsi_flat_neutral_funding(self):
        """RSI FLAT + neutral funding → FLAT."""
        assert self._apply_funding_rules("FLAT", 0.0) == "FLAT"
        assert self._apply_funding_rules("FLAT", 1.0) == "FLAT"
        assert self._apply_funding_rules("FLAT", -1.0) == "FLAT"

    def test_rsi_buy_zero_funding(self):
        """RSI BUY + z=0 (funding neutral) → BUY (confluence passes)."""
        assert self._apply_funding_rules("BUY", 0.0) == "BUY"

    def test_rsi_sell_zero_funding(self):
        """RSI SELL + z=0 → SELL."""
        assert self._apply_funding_rules("SELL", 0.0) == "SELL"


class TestExchangeFundingMethod:
    """Test that BinanceFutures has funding rate methods (interface check)."""

    def test_method_exists(self):
        """BinanceFutures should have fetch_funding_rate and fetch_funding_rate_history."""
        from trader.exchange import BinanceFutures
        assert hasattr(BinanceFutures, "fetch_funding_rate")
        assert hasattr(BinanceFutures, "fetch_funding_rate_history")


class TestConfigFundingParams:
    """Test that TraderConfig has funding rate parameters."""

    def test_defaults(self):
        """TraderConfig should have funding rate defaults."""
        from trader.config import TraderConfig
        cfg = TraderConfig()
        assert cfg.funding_rate_enabled is True
        assert cfg.funding_zscore_period == 30
        assert cfg.funding_zscore_threshold == 2.0
        assert cfg.funding_zscore_extreme == 3.0

    def test_yaml_loading(self):
        """Funding rate params should load from YAML."""
        from trader.config import TraderConfig
        cfg = TraderConfig.from_yaml()
        assert cfg.funding_rate_enabled is True
