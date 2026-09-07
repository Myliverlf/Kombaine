"""Tests for data_loader — pytest fixtures ≥3, AST-guard, dry-run only.

Fixtures:
    real_csv_dir: path to real tinkoff_futures_data directory
    synthetic_dir: temporary directory with no CSV files (forces fallback)
    sample_tickers: list of tickers from the real data universe
    excluded_tickers: default excluded list ["RI"]

Tests:
    test_real_csv_loads: real SBER CSV loads with correct columns
    test_synthetic_fallback: missing file triggers synthetic OHLCV
    test_excluded_ticker_raises: RI is rejected
    test_invalid_interval_raises: bad interval is rejected
    test_scorecard_integration: data feeds allocator_metrics scorecard
    test_load_universe_multi: multiple tickers load correctly

AST-guard: no broker/live imports (tinkoff, alpaca, ib, etc.)
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

# ─── AST-guard: no broker imports ─────────────────────────────────────


def _assert_no_broker():
    """AST-guard: ensures no broker imports in this test module."""
    source = inspect.getsource(inspect.getmodule(_assert_no_broker))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.lower()
                assert "tinkoff" not in name, (
                    f"Broker import detected in tests: {alias.name}"
                )
                assert "alpaca" not in name, (
                    f"Broker import detected in tests: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.lower()
                assert "tinkoff" not in mod, (
                    f"Broker import detected in tests: {node.module}"
                )
                assert "alpaca" not in mod, (
                    f"Broker import detected in tests: {node.module}"
                )


_assert_no_broker()

# ─── Add project code/ to path ────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CODE_DIR = str(_PROJECT_ROOT / "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from data_loader import (
    DEFAULT_EXCLUDED,
    DEFAULT_DATA_DIR,
    CSV_COLUMNS,
    _generate_synthetic_ohlcv,
    _read_csv,
    load_ohlcv,
    load_universe,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def real_csv_dir() -> Path:
    """Path to real tinkoff_futures_data directory."""
    return DEFAULT_DATA_DIR


@pytest.fixture
def synthetic_dir(tmp_path: Path) -> Path:
    """Temporary empty directory — forces synthetic fallback."""
    return tmp_path


@pytest.fixture
def sample_tickers() -> List[str]:
    """List of tickers known to exist in tinkoff_futures_data."""
    return ["SBER", "LKOH", "GAZP"]


@pytest.fixture
def excluded_tickers() -> List[str]:
    """Default excluded tickers list."""
    return DEFAULT_EXCLUDED.copy()


# ═══════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestRealCSV:
    """Tests using real tinkoff_futures_data CSV files."""

    def test_real_csv_loads(self, real_csv_dir: Path, sample_tickers: List[str]):
        """Real SBER CSV loads with correct columns and rows > 0."""
        ticker = sample_tickers[0]
        df = load_ohlcv(ticker, "15m", real_csv_dir)
        assert len(df) > 0, f"Real CSV for {ticker} returned empty DataFrame"
        assert list(df.columns) == CSV_COLUMNS, (
            f"Expected columns {CSV_COLUMNS}, got {list(df.columns)}"
        )
        assert df.attrs.get("source") == "real", "Should report source=real"
        assert df.attrs.get("ticker") == ticker
        # Time column should be datetime
        assert df["time"].dtype.kind == "M" or hasattr(df["time"].dt, "tz")
        # Numeric columns
        for col in ["open", "high", "low", "close"]:
            assert df[col].dtype.kind == "f", f"{col} should be float"
        assert df["volume"].dtype.kind in ("i", "u"), "volume should be int"

    def test_real_csv_all_tickers(self, real_csv_dir: Path):
        """All sample tickers load with real data when files exist."""
        tickers = ["BR", "GAZP", "LKOH", "SBER", "Si"]
        for t in tickers:
            df = load_ohlcv(t, "15m", real_csv_dir)
            assert len(df) > 100, (
                f"Real CSV for {t} should have >100 rows, got {len(df)}"
            )


class TestSyntheticFallback:
    """Tests for synthetic fallback behavior."""

    def test_synthetic_fallback(self, synthetic_dir: Path):
        """Missing CSV file triggers synthetic OHLCV generation."""
        df = load_ohlcv("NONEXIST", "15m", synthetic_dir)
        assert len(df) == 200, f"Expected 200 rows, got {len(df)}"
        assert list(df.columns) == CSV_COLUMNS
        assert df.attrs.get("source") == "synthetic"
        assert df.attrs.get("ticker") == "NONEXIST"
        # Prices should be positive
        assert (df["close"] > 0).all(), "All close prices must be positive"
        assert (df["high"] >= df["low"]).all(), "high >= low"
        assert (df["volume"] > 0).all(), "All volumes must be positive"

    def test_synthetic_reproducible(self, synthetic_dir: Path):
        """Same ticker produces identical synthetic data."""
        df1 = load_ohlcv("SBER", "15m", synthetic_dir)
        df2 = load_ohlcv("SBER", "15m", synthetic_dir)
        pd.testing.assert_frame_equal(df1, df2)

    def test_synthetic_different_tickers(self, synthetic_dir: Path):
        """Different tickers produce different synthetic data."""
        df1 = load_ohlcv("SBER", "15m", synthetic_dir)
        df2 = load_ohlcv("LKOH", "15m", synthetic_dir)
        assert not df1["close"].equals(df2["close"]), (
            "Different tickers should produce different prices"
        )


class TestExclusion:
    """Tests for excluded ticker filtering."""

    def test_excluded_ticker_raises(self, excluded_tickers: List[str]):
        """RI (excluded) raises ValueError."""
        with pytest.raises(ValueError, match="excluded"):
            load_ohlcv("RI", "15m")

    def test_excluded_case_insensitive(self):
        """Exclusion check is case-insensitive."""
        with pytest.raises(ValueError, match="excluded"):
            load_ohlcv("ri", "15m")

    def test_custom_excluded_list(self):
        """Custom exclusion list works."""
        with pytest.raises(ValueError, match="excluded"):
            load_ohlcv("SBER", "15m", excluded=["SBER"])

    def test_non_excluded_passes(self, synthetic_dir: Path):
        """Non-excluded ticker loads fine."""
        df = load_ohlcv("SBER", "15m", synthetic_dir)
        assert len(df) > 0


class TestValidation:
    """Tests for input validation."""

    def test_invalid_interval_raises(self, synthetic_dir: Path):
        """Invalid interval raises ValueError."""
        with pytest.raises(ValueError, match="Invalid interval"):
            load_ohlcv("SBER", "5m", synthetic_dir)

    def test_valid_intervals(self, synthetic_dir: Path):
        """Both valid intervals work."""
        for iv in ["15m", "1h"]:
            df = load_ohlcv("SBER", iv, synthetic_dir)
            assert len(df) > 0


class TestScorecardIntegration:
    """Integration test: data_loader feeds allocator_metrics scorecard."""

    def test_scorecard_integration(self, real_csv_dir: Path):
        """DataFrame from data_loader can be used for expectancy_r computation."""
        import math

        df = load_ohlcv("SBER", "15m", real_csv_dir)
        # Compute simple stats from OHLCV
        returns = df["close"].pct_change().dropna()
        up_moves = returns[returns > 0]
        down_moves = returns[returns < 0]

        win_rate = len(up_moves) / len(returns) if len(returns) > 0 else 0.0
        avg_win = float(up_moves.mean()) if len(up_moves) > 0 else 0.0
        avg_loss = float(down_moves.abs().mean()) if len(down_moves) > 0 else 0.0

        # Build stats dict for allocator_metrics.expectancy_r
        stats = {
            "win_rate": win_rate,
            "avg_win": avg_win * 1000,  # scale to rub-like
            "avg_loss": avg_loss * 1000,
        }

        # Import allocator_metrics
        from allocator_metrics import expectancy_r, risk_penalty

        er = expectancy_r(stats, risk_per_trade=1.0)
        assert isinstance(er, float), "expectancy_r should return float"
        assert not math.isnan(er), "expectancy_r should not be NaN"

        rp = risk_penalty({"drawdown_pct": 5.0, "volatility": 2.0})
        assert isinstance(rp, float), "risk_penalty should return float"
        assert 0.0 <= rp <= 1.0, f"risk_penalty should be 0..1, got {rp}"


class TestLoadUniverse:
    """Tests for load_universe multi-ticker loading."""

    def test_load_universe_multi(self, synthetic_dir: Path):
        """Multiple tickers load via load_universe."""
        tickers = ["SBER", "LKOH", "GAZP"]
        result = load_universe(tickers, "15m", synthetic_dir)
        assert len(result) == 3, f"Expected 3 tickers, got {len(result)}"
        for t in tickers:
            assert t in result, f"{t} missing from result"
            assert len(result[t]) > 0

    def test_load_universe_skips_excluded(self, synthetic_dir: Path):
        """load_universe silently skips excluded tickers."""
        tickers = ["SBER", "RI", "LKOH"]
        result = load_universe(tickers, "15m", synthetic_dir)
        assert "RI" not in result, "RI should be excluded"
        assert "SBER" in result
        assert "LKOH" in result
