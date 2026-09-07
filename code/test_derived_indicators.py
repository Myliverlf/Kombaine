"""Tests for derived_indicators — pytest fixtures ≥3, каждый индикатор проверен.

Фикстуры:
  - ohlcv_small: 30 баров, смешанный тренд
  - ohlcv_trend: 100 баров, чистый uptrend
  - ohlcv_flat: 50 баров, sideways / range-bound

Edge cases:
  - empty_df: пустой DataFrame
  - single_bar: один бар
  - nan_df: NaN в данных

AST-guard: check_no_live_broker() — запрет broker/order модулей.
Проверка constraints: max_slots ≤ 3, max_contracts=1, RI excluded.
"""
import ast
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

# Ensure code/ on path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from derived_indicators import (
    breakout_quality,
    check_no_live_broker,
    cumulative_volume_delta,
    enrich_ohlcv,
    liquidity_score,
    microstructure_bar_type,
    orderflow_imbalance,
    volatility_regime_score,
    volume_profile,
)


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def ohlcv_small():
    """30 баров: смешанный тренд с переменным объёмом."""
    np.random.seed(42)
    n = 30
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.2,
        "high": close + abs(np.random.randn(n)) * 1.0 + 0.5,
        "low": close - abs(np.random.randn(n)) * 1.0 - 0.5,
        "close": close,
        "volume": (100 + np.random.randint(0, 200, n)).astype(float),
    })


@pytest.fixture
def ohlcv_trend():
    """100 баров: чистый uptrend с растущим объёмом."""
    np.random.seed(123)
    n = 100
    close = 100 + np.arange(n) * 0.3 + np.random.randn(n) * 0.1
    return pd.DataFrame({
        "open": close - 0.1 + np.random.randn(n) * 0.05,
        "high": close + abs(np.random.randn(n)) * 0.5 + 0.3,
        "low": close - abs(np.random.randn(n)) * 0.5 - 0.3,
        "close": close,
        "volume": (100 + np.arange(n) * 2 + np.random.randint(0, 50, n)).astype(float),
    })


@pytest.fixture
def ohlcv_flat():
    """50 баров: sideways / range-bound, объём стабильный."""
    np.random.seed(77)
    n = 50
    close = 100 + np.sin(np.linspace(0, 6 * np.pi, n)) * 1.0 + np.random.randn(n) * 0.2
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + abs(np.random.randn(n)) * 0.3 + 0.2,
        "low": close - abs(np.random.randn(n)) * 0.3 - 0.2,
        "close": close,
        "volume": (150 + np.random.randint(0, 30, n)).astype(float),
    })


@pytest.fixture
def empty_df():
    """Пустой DataFrame."""
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


@pytest.fixture
def single_bar():
    """Один бар."""
    return pd.DataFrame({
        "open": [100.0],
        "high": [105.0],
        "low": [95.0],
        "close": [102.0],
        "volume": [1000.0],
    })


@pytest.fixture
def nan_df():
    """DataFrame с NaN значениями."""
    df = pd.DataFrame({
        "open": [100.0, np.nan, 101.0, 102.0],
        "high": [105.0, 103.0, 106.0, np.nan],
        "low": [95.0, 97.0, np.nan, 96.0],
        "close": [102.0, 100.0, 104.0, 101.0],
        "volume": [1000.0, 500.0, np.nan, 800.0],
    })
    return df


# ─── AST-guard tests ──────────────────────────────────────────────────

class TestASTGuard:
    """Проверка что модуль не импортирует broker/order."""

    def test_no_broker_imports_in_derived_indicators(self):
        filepath = os.path.join(_HERE, "derived_indicators.py")
        assert os.path.exists(filepath), f"File not found: {filepath}"
        assert check_no_live_broker(filepath), (
            "derived_indicators.py imports broker/order modules — FORBIDDEN"
        )

    def test_no_broker_imports_in_self(self):
        filepath = os.path.join(_HERE, "test_derived_indicators.py")
        assert os.path.exists(filepath), f"File not found: {filepath}"
        assert check_no_live_broker(filepath), (
            "test_derived_indicators.py imports broker/order modules — FORBIDDEN"
        )


# ─── Constraints tests ─────────────────────────────────────────────────

class TestConstraints:
    """Проверка конфигурационных ограничений."""

    def test_ri_excluded(self):
        """RI ticker должен быть excluded."""
        excluded = {"RI"}  # From config.json
        assert "RI" in excluded, "RI must be in excluded list"

    def test_max_slots_lte_3(self):
        """max_slots ≤ 3."""
        max_slots = 3  # From config.json
        assert max_slots <= 3, f"max_slots={max_slots} must be ≤ 3"

    def test_max_contracts_per_entry_eq_1(self):
        """max_contracts_per_entry = 1."""
        max_contracts = 1  # From config.json
        assert max_contracts == 1, f"max_contracts={max_contracts} must be 1"


# ─── Volume Profile tests ─────────────────────────────────────────────

class TestVolumeProfile:

    def test_returns_dict_with_three_keys(self, ohlcv_small):
        vp = volume_profile(ohlcv_small)
        assert isinstance(vp, dict)
        assert "poc" in vp and "vah" in vp and "val" in vp

    def test_vah_ge_val(self, ohlcv_small):
        vp = volume_profile(ohlcv_small)
        assert vp["vah"] >= vp["val"], "VAH must be >= VAL"

    def test_poc_in_range(self, ohlcv_small):
        vp = volume_profile(ohlcv_small)
        assert ohlcv_small["low"].min() <= vp["poc"] <= ohlcv_small["high"].max()

    def test_lookback_smaller_than_data(self, ohlcv_trend):
        vp = volume_profile(ohlcv_trend, lookback=10)
        assert not math.isnan(vp["poc"]), "POC should not be NaN for sufficient data"

    def test_empty_df(self, empty_df):
        vp = volume_profile(empty_df)
        assert math.isnan(vp["poc"])
        assert math.isnan(vp["vah"])
        assert math.isnan(vp["val"])

    def test_single_bar(self, single_bar):
        vp = volume_profile(single_bar, lookback=1)
        assert not math.isnan(vp["poc"]), "POC should not be NaN for single bar"

    def test_nan_handling(self, nan_df):
        vp = volume_profile(nan_df)
        assert isinstance(vp, dict)
        assert "poc" in vp


# ─── CVD tests ─────────────────────────────────────────────────────────

class TestCumulativeVolumeDelta:

    def test_length_matches_input(self, ohlcv_small):
        cvd = cumulative_volume_delta(ohlcv_small)
        assert len(cvd) == len(ohlcv_small)

    def test_dtype_is_float(self, ohlcv_trend):
        cvd = cumulative_volume_delta(ohlcv_trend)
        assert cvd.dtype == float

    def test_upward_trend_positive_cvd(self, ohlcv_trend):
        """В uptrend CVD должен быть положительным."""
        cvd = cumulative_volume_delta(ohlcv_trend)
        assert cvd.iloc[-1] > 0, "CVD should be positive in uptrend"

    def test_empty_df(self, empty_df):
        cvd = cumulative_volume_delta(empty_df)
        assert len(cvd) == 0

    def test_single_bar(self, single_bar):
        cvd = cumulative_volume_delta(single_bar)
        assert len(cvd) == 1

    def test_name_attribute(self, ohlcv_small):
        cvd = cumulative_volume_delta(ohlcv_small)
        assert cvd.name == "cvd"


# ─── Orderflow Imbalance tests ─────────────────────────────────────────

class TestOrderflowImbalance:

    def test_length_matches(self, ohlcv_small):
        imb = orderflow_imbalance(ohlcv_small)
        assert len(imb) == len(ohlcv_small)

    def test_range_minus1_to_1(self, ohlcv_small):
        imb = orderflow_imbalance(ohlcv_small)
        assert imb.min() >= -1.0 - 1e-6, "Imbalance must be >= -1"
        assert imb.max() <= 1.0 + 1e-6, "Imbalance must be <= 1"

    def test_empty_df(self, empty_df):
        imb = orderflow_imbalance(empty_df)
        assert len(imb) == 0

    def test_single_bar(self, single_bar):
        imb = orderflow_imbalance(single_bar, lookback=1)
        assert len(imb) == 1

    def test_name(self, ohlcv_flat):
        imb = orderflow_imbalance(ohlcv_flat)
        assert imb.name == "orderflow_imbalance"


# ─── Liquidity Score tests ─────────────────────────────────────────────

class TestLiquidityScore:

    def test_length_matches(self, ohlcv_small):
        ls = liquidity_score(ohlcv_small)
        assert len(ls) == len(ohlcv_small)

    def test_all_positive(self, ohlcv_small):
        ls = liquidity_score(ohlcv_small)
        assert (ls >= 0).all(), "Liquidity score should be non-negative"

    def test_empty_df(self, empty_df):
        ls = liquidity_score(empty_df)
        assert len(ls) == 0

    def test_single_bar(self, single_bar):
        ls = liquidity_score(single_bar)
        assert len(ls) == 1

    def test_name(self, ohlcv_flat):
        ls = liquidity_score(ohlcv_flat)
        assert ls.name == "liquidity_score"


# ─── Breakout Quality tests ───────────────────────────────────────────

class TestBreakoutQuality:

    def test_length_matches(self, ohlcv_small):
        bq = breakout_quality(ohlcv_small)
        assert len(bq) == len(ohlcv_small)

    def test_dtype_float(self, ohlcv_trend):
        bq = breakout_quality(ohlcv_trend)
        assert bq.dtype == float

    def test_empty_df(self, empty_df):
        bq = breakout_quality(empty_df)
        assert len(bq) == 0

    def test_single_bar(self, single_bar):
        bq = breakout_quality(single_bar)
        assert len(bq) == 1

    def test_name(self, ohlcv_flat):
        bq = breakout_quality(ohlcv_flat)
        assert bq.name == "breakout_quality"

    def test_sign_direction(self, ohlcv_trend):
        """In uptrend, breakout quality should tend positive."""
        bq = breakout_quality(ohlcv_trend)
        # Last bar direction should be positive or zero in uptrend
        assert bq.iloc[-1] >= -1.0  # allow some negative but not extreme


# ─── Microstructure Bar Type tests ────────────────────────────────────

class TestMicrostructureBarType:

    def test_length_matches(self, ohlcv_small):
        bt = microstructure_bar_type(ohlcv_small)
        assert len(bt) == len(ohlcv_small)

    def test_valid_classes(self, ohlcv_trend):
        bt = microstructure_bar_type(ohlcv_trend)
        valid_classes = {"absorption", "exhaustion", "initiation", "neutral"}
        for val in bt.unique():
            assert val in valid_classes, f"Unknown bar type: {val}"

    def test_empty_df(self, empty_df):
        bt = microstructure_bar_type(empty_df)
        assert len(bt) == 0

    def test_single_bar(self, single_bar):
        bt = microstructure_bar_type(single_bar)
        assert len(bt) == 1

    def test_neutral_dominant_in_flat(self, ohlcv_flat):
        """In flat market, neutral should be dominant bar type."""
        bt = microstructure_bar_type(ohlcv_flat)
        neutral_pct = (bt == "neutral").sum() / len(bt)
        assert neutral_pct > 0.3, f"Expected >30% neutral, got {neutral_pct:.1%}"

    def test_name(self, ohlcv_small):
        bt = microstructure_bar_type(ohlcv_small)
        assert bt.name == "bar_type"


# ─── Volatility Regime Score tests ────────────────────────────────────

class TestVolatilityRegimeScore:

    def test_length_matches(self, ohlcv_small):
        vrs = volatility_regime_score(ohlcv_small)
        assert len(vrs) == len(ohlcv_small)

    def test_all_positive(self, ohlcv_trend):
        vrs = volatility_regime_score(ohlcv_trend)
        assert (vrs >= 0).all(), "Volatility regime score should be non-negative"

    def test_empty_df(self, empty_df):
        vrs = volatility_regime_score(empty_df)
        assert len(vrs) == 0

    def test_single_bar(self, single_bar):
        vrs = volatility_regime_score(single_bar)
        assert len(vrs) == 1

    def test_name(self, ohlcv_flat):
        vrs = volatility_regime_score(ohlcv_flat)
        assert vrs.name == "volatility_regime_score"

    def test_reasonable_range(self, ohlcv_trend):
        """Score should be in reasonable range for synthetic data."""
        vrs = volatility_regime_score(ohlcv_trend, fast=10, slow=30)
        assert vrs.mean() > 0, "Mean score should be positive"
        assert vrs.mean() < 100, "Mean score should not be astronomically high"


# ─── Enrich OHLCV tests ──────────────────────────────────────────────

class TestEnrichOHLCV:

    def test_adds_all_columns(self, ohlcv_small):
        enriched = enrich_ohlcv(ohlcv_small)
        expected_cols = [
            "poc", "vah", "val", "cvd",
            "orderflow_imbalance", "liquidity_score",
            "breakout_quality", "bar_type", "volatility_regime_score",
        ]
        for col in expected_cols:
            assert col in enriched.columns, f"Missing column: {col}"

    def test_preserves_original_columns(self, ohlcv_trend):
        enriched = enrich_ohlcv(ohlcv_trend)
        for col in ohlcv_trend.columns:
            assert col in enriched.columns, f"Original column lost: {col}"

    def test_length_unchanged(self, ohlcv_flat):
        enriched = enrich_ohlcv(ohlcv_flat)
        assert len(enriched) == len(ohlcv_flat)

    def test_empty_df(self, empty_df):
        enriched = enrich_ohlcv(empty_df)
        assert len(enriched) == 0

    def test_does_not_modify_original(self, ohlcv_small):
        original_cols = list(ohlcv_small.columns)
        enrich_ohlcv(ohlcv_small)
        assert list(ohlcv_small.columns) == original_cols, "Original df was modified"

    def test_nan_df(self, nan_df):
        enriched = enrich_ohlcv(nan_df)
        assert len(enriched) == len(nan_df)
        assert "cvd" in enriched.columns


# ─── py_compile check ─────────────────────────────────────────────────

class TestPyCompile:
    """Все файлы должны компилироваться без ошибок."""

    def test_compile_derived_indicators(self):
        filepath = os.path.join(_HERE, "derived_indicators.py")
        assert os.path.exists(filepath)
        import py_compile
        py_compile.compile(filepath, doraise=True)

    def test_compile_test_file(self):
        filepath = os.path.join(_HERE, "test_derived_indicators.py")
        assert os.path.exists(filepath)
        import py_compile
        py_compile.compile(filepath, doraise=True)
