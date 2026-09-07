"""Tests for multi_tf_features module.

≥3 pytest fixtures required (task.md AC2).
Covers: aggregate_ohlcv, multi_tf_features, signal_freshness_score,
        alignment_score, regime_context_score, constraint checks.
No live broker — validated via check_no_live_broker at module level.
"""
import math
import sys
import os

import pandas as pd
import numpy as np
import pytest

# Ensure code/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code"))

from multi_tf_features import (
    aggregate_ohlcv,
    multi_tf_features,
    signal_freshness_score,
    alignment_score,
    regime_context_score,
    check_no_live_broker,
    enrich_with_multi_tf,
)


# ─── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def ohlcv_15m_50bars():
    """50 баров 15m OHLCV с DatetimeIndex, синтетический uptrend."""
    n = 50
    idx = pd.date_range("2025-06-01", periods=n, freq="15min")
    # Uptrend: close drifts upward
    base_price = 100.0
    closes = [base_price + i * 0.5 + np.random.normal(0, 0.3) for i in range(n)]
    df = pd.DataFrame({
        "open": [c - 0.2 for c in closes],
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [100 + i * 2 for i in range(n)],
    }, index=idx)
    return df


@pytest.fixture
def ohlcv_15m_100bars():
    """100 баров 15m OHLCV с DatetimeIndex, mixed regime (trend + range)."""
    n = 100
    idx = pd.date_range("2025-06-01", periods=n, freq="15min")
    # First 50 bars: uptrend, next 50: range
    closes = []
    for i in range(n):
        if i < 50:
            closes.append(100.0 + i * 0.5 + np.random.normal(0, 0.3))
        else:
            closes.append(125.0 + np.random.normal(0, 1.5))
    df = pd.DataFrame({
        "open": [c - 0.2 for c in closes],
        "high": [c + 0.8 for c in closes],
        "low": [c - 0.8 for c in closes],
        "close": closes,
        "volume": [100 + (i % 20) * 5 for i in range(n)],
    }, index=idx)
    return df


@pytest.fixture
def regime_snapshot():
    """Regime snapshot dict с 3 тикерами (разные regime/vol)."""
    return {
        "tickers": {
            "BR": {"adx": 40, "direction": "up", "regime": "trend", "vol_bucket": "high"},
            "GAZP": {"adx": 15, "direction": "down", "regime": "range", "vol_bucket": "low"},
            "SBER": {"adx": 25, "direction": "up", "regime": "trend", "vol_bucket": "medium"},
        },
        "bias": "up",
    }


@pytest.fixture
def signals_list():
    """Список из 5 кандидатов-сигналов с direction."""
    return [
        {"ticker": "BR", "direction": "LONG", "score": 0.8},
        {"ticker": "GAZP", "direction": "LONG", "score": 0.6},
        {"ticker": "LKOH", "direction": "LONG", "score": 0.5},
        {"ticker": "SBER", "direction": "SHORT", "score": 0.3},
        {"ticker": "Si", "direction": None, "score": 0.1},
    ]


@pytest.fixture
def signals_aligned():
    """Все 3 стратегии согласны (LONG)."""
    return [
        {"ticker": "BR", "direction": "LONG", "score": 0.9},
        {"ticker": "GAZP", "direction": "LONG", "score": 0.7},
        {"ticker": "SBER", "direction": "LONG", "score": 0.6},
    ]


@pytest.fixture
def signals_split():
    """Равный split: 2 LONG + 2 SHORT."""
    return [
        {"ticker": "BR", "direction": "LONG"},
        {"ticker": "GAZP", "direction": "LONG"},
        {"ticker": "SBER", "direction": "SHORT"},
        {"ticker": "LKOH", "direction": "SHORT"},
    ]


# ─── Tests: aggregate_ohlcv ────────────────────────────────────────────

class TestAggregateOhlcv:
    def test_basic_resample_1h(self, ohlcv_15m_50bars):
        """50 баров 15m → ~12 hourly баров."""
        out = aggregate_ohlcv(ohlcv_15m_50bars, target_tf="1h")
        assert len(out) >= 1, "Должен быть хотя бы 1 hourly бар"
        assert len(out) < len(ohlcv_15m_50bars), "Hourly должны быть компактнее 15m"
        for col in ("open", "high", "low", "close", "volume"):
            assert col in out.columns, f"Missing column: {col}"

    def test_empty_df(self):
        """Пустой DataFrame → пустой результат."""
        out = aggregate_ohlcv(pd.DataFrame(), target_tf="1h")
        assert len(out) == 0

    def test_ohlcv_integrity(self, ohlcv_15m_50bars):
        """high >= low и high >= open, high >= close в каждом баре."""
        out = aggregate_ohlcv(ohlcv_15m_50bars, target_tf="1h")
        if len(out) > 0:
            assert (out["high"] >= out["low"]).all()
            assert (out["high"] >= out["open"]).all()
            assert (out["high"] >= out["close"]).all()


# ─── Tests: multi_tf_features ──────────────────────────────────────────

class TestMultiTfFeatures:
    def test_columns_present(self, ohlcv_15m_50bars):
        """multi_tf_features добавляет 3 колонки."""
        out = multi_tf_features(ohlcv_15m_50bars)
        assert "daily_trend_dir" in out.columns
        assert "hourly_momentum" in out.columns
        assert "entry_quality" in out.columns
        assert len(out) == len(ohlcv_15m_50bars)

    def test_daily_trend_range(self, ohlcv_15m_100bars):
        """daily_trend_dir ∈ {-1, 0, +1}."""
        out = multi_tf_features(ohlcv_15m_100bars)
        vals = out["daily_trend_dir"].unique()
        assert set(vals).issubset({-1.0, 0.0, 1.0})

    def test_entry_quality_range(self, ohlcv_15m_100bars):
        """entry_quality ∈ [-1, +1]."""
        out = multi_tf_features(ohlcv_15m_100bars)
        assert (out["entry_quality"] >= -1.0001).all()
        assert (out["entry_quality"] <= 1.0001).all()

    def test_small_df(self):
        """DataFrame с 1 баром — не падает, возвращает нулевые фичи."""
        idx = pd.date_range("2025-01-01", periods=1, freq="15min")
        df = pd.DataFrame({
            "open": [100], "high": [101], "low": [99],
            "close": [100.5], "volume": [50],
        }, index=idx)
        out = multi_tf_features(df)
        assert "daily_trend_dir" in out.columns
        assert len(out) == 1


# ─── Tests: signal_freshness_score ─────────────────────────────────────

class TestSignalFreshness:
    @pytest.mark.parametrize("age,expected", [
        (0, 1.0),
        (480, 0.5),
        (960, 0.0),
        (1200, 0.0),  # oversize → clipped to 0
    ])
    def test_freshness_values(self, age, expected):
        """Freshness score при разных age."""
        result = signal_freshness_score(age, max_age=960)
        assert math.isclose(result, expected, abs_tol=1e-6)

    def test_negative_age(self):
        """Отрицательный age → 1.0 (clip)."""
        assert signal_freshness_score(-100) == 1.0

    def test_zero_max_age(self):
        """max_age=0 → 0.0 (avoid division by zero)."""
        assert signal_freshness_score(10, max_age=0) == 0.0


# ─── Tests: alignment_score ────────────────────────────────────────────

class TestAlignment:
    def test_all_aligned(self, signals_aligned):
        """Все LONG → alignment = 1.0."""
        assert math.isclose(alignment_score(signals_aligned), 1.0)

    def test_split(self, signals_split):
        """2 LONG + 2 SHORT → alignment = 0.5."""
        assert math.isclose(alignment_score(signals_split), 0.5)

    def test_single_signal(self):
        """Один сигнал → alignment = 1.0 (нечего сравнивать)."""
        assert alignment_score([{"direction": "LONG"}]) == 1.0

    def test_empty(self):
        """Пустой список → 0.0."""
        assert alignment_score([]) == 0.0

    def test_all_none(self):
        """Все direction=None → 0.0."""
        assert alignment_score([{"direction": None}, {"direction": None}]) == 0.0


# ─── Tests: regime_context_score ───────────────────────────────────────

class TestRegimeContext:
    def test_trend_high_adx(self, regime_snapshot):
        """BR: trend + adx=40 + high vol → score > 0.5."""
        score = regime_context_score(regime_snapshot, "BR")
        assert score > 0.5

    def test_range_low_adx(self, regime_snapshot):
        """GAZP: range + adx=15 + low vol → score < 0.5."""
        score = regime_context_score(regime_snapshot, "GAZP")
        assert score < 0.5

    def test_unknown_ticker(self, regime_snapshot):
        """Неизвестный тикер → 0.1 (minimal confidence)."""
        score = regime_context_score(regime_snapshot, "UNKNOWN")
        assert math.isclose(score, 0.1)

    def test_score_range(self, regime_snapshot):
        """Все scores ∈ [0, 1]."""
        for ticker in regime_snapshot["tickers"]:
            s = regime_context_score(regime_snapshot, ticker)
            assert 0.0 <= s <= 1.0


# ─── Tests: Constraint checks (AC6, AC7, AC8) ─────────────────────────

class TestConstraints:
    def test_ri_excluded(self):
        """RI не должен присутствовать в universe (config: excluded=["RI"])."""
        import json
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        with open(config_path) as f:
            config = json.load(f)
        assert "RI" in config.get("excluded", []), "RI must be in excluded list"

    def test_max_slots_leq_3(self):
        """max_slots ≤ 3."""
        import json
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        with open(config_path) as f:
            config = json.load(f)
        assert config["risk"]["max_slots"] <= 3

    def test_max_contracts_1(self):
        """max_contracts_per_entry == 1."""
        import json
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        with open(config_path) as f:
            config = json.load(f)
        assert config["risk"]["max_contracts_per_entry"] == 1


# ─── AST-guard: no broker in test file itself ──────────────────────────

class TestNoBroker:
    def test_no_live_broker_in_test_file(self):
        """AST-guard: test file не содержит broker imports."""
        test_path = os.path.abspath(__file__)
        # This test file itself should have no broker imports
        result = check_no_live_broker(test_path)
        assert result, "Test file contains broker imports — FAIL"

    def test_no_live_broker_in_module(self):
        """AST-guard: multi_tf_features.py не содержит broker imports."""
        module_path = os.path.join(os.path.dirname(__file__), "..", "code", "multi_tf_features.py")
        assert check_no_live_broker(module_path)


# ─── Tests: enrich_with_multi_tf ───────────────────────────────────────

class TestEnrich:
    def test_enrich_output(self, ohlcv_15m_50bars):
        """enrich_with_multi_tf добавляет multi-TF колонки."""
        out = enrich_with_multi_tf(ohlcv_15m_50bars)
        assert "daily_trend_dir" in out.columns
        assert "entry_quality" in out.columns
        assert len(out) == len(ohlcv_15m_50bars)

    def test_enrich_preserves_original(self, ohlcv_15m_50bars):
        """enrich_with_multi_tf не модифицирует исходный df."""
        original_cols = set(ohlcv_15m_50bars.columns)
        _ = enrich_with_multi_tf(ohlcv_15m_50bars)
        assert set(ohlcv_15m_50bars.columns) == original_cols
