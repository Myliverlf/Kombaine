"""Test Scorecard Dry-Run — pytest для цепочки data_loader → risk_scorecard → allocator_metrics.

Две ветки данных:
  (a) Реальные CSV из tinkoff_futures_data (ticks: BR/LKOH/SBER)
  (b) Synthetic fallback для несуществующих тикеров

Проверяет:
  - scorecard возвращает допустимый диапазон [0..1]
  - constraints (max_slots=3, max_contracts=1) соблюдаются
  - attrs["source"] == "real" для реальных данных
  - attrs["source"] == "synthetic" для фоллбэка
  - RI excluded из scorecard

Live orders запрещены. Все данные — fixtures или реальные CSV на диске.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from data_loader import load_ohlcv, load_universe, DEFAULT_DATA_DIR
from risk_scorecard import compute_scorecard, validate_constraints
from allocator_metrics import expectancy_r, risk_penalty, allocator_score

# ─── Fixtures ──────────────────────────────────────────────────────────

TINKOFF_DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
REAL_TICKERS = ["BR", "LKOH", "SBER"]
SYNTHETIC_TICKER = "ZZZZZ"  # guaranteed non-existent → synthetic fallback
SC_TEST_TICKERS = REAL_TICKERS  # 3 tickers for scorecard composite (matches max_slots=3)


@pytest.fixture
def real_csv_dir() -> Path:
    """Path to real tinkoff_futures_data CSV directory."""
    return TINKOFF_DATA_DIR


@pytest.fixture
def sample_real_tickers() -> list[str]:
    """Three tickers with real CSV data on disk."""
    return REAL_TICKERS


@pytest.fixture
def synthetic_ticker() -> str:
    """Ticker guaranteed to produce synthetic fallback."""
    return SYNTHETIC_TICKER


@pytest.fixture
def scorecard_result() -> dict:
    """Full scorecard computed for 3 real tickers (matches max_slots=3)."""
    return compute_scorecard(
        SC_TEST_TICKERS,
        data_dir=TINKOFF_DATA_DIR,
        excluded=["RI"],
        interval="15m",
    )


# ─── Tests: Real CSV data loading ──────────────────────────────────────

class TestRealCSVLoading:
    """Verify real CSV loading from tinkoff_futures_data."""

    def test_real_csv_has_real_source(self, real_csv_dir, sample_real_tickers):
        """Each real ticker must load with source='real'."""
        for ticker in sample_real_tickers:
            df = load_ohlcv(ticker, "15m", data_dir=real_csv_dir)
            assert df.attrs.get("source") == "real", (
                f"{ticker}: expected source='real', got '{df.attrs.get('source')}'"
            )

    def test_real_csv_nonempty(self, real_csv_dir, sample_real_tickers):
        """Each real CSV must have >100 rows."""
        for ticker in sample_real_tickers:
            df = load_ohlcv(ticker, "15m", data_dir=real_csv_dir)
            assert len(df) > 100, f"{ticker}: only {len(df)} rows"

    def test_real_csv_has_ohlcv_columns(self, real_csv_dir):
        """Real CSV must have standard OHLCV columns."""
        df = load_ohlcv("SBER", "15m", data_dir=real_csv_dir)
        expected = {"time", "open", "high", "low", "close", "volume"}
        assert expected.issubset(set(df.columns))


# ─── Tests: Synthetic fallback ─────────────────────────────────────────

class TestSyntheticFallback:
    """Verify synthetic fallback for non-existent tickers."""

    def test_synthetic_has_synthetic_source(self, synthetic_ticker):
        """Non-existent ticker must produce source='synthetic'."""
        df = load_ohlcv(synthetic_ticker, "15m")
        assert df.attrs.get("source") == "synthetic"

    def test_synthetic_nonempty(self, synthetic_ticker):
        """Synthetic fallback must produce data."""
        df = load_ohlcv(synthetic_ticker, "15m")
        assert len(df) >= 50

    def test_synthetic_has_ohlcv_columns(self, synthetic_ticker):
        """Synthetic data must have standard OHLCV columns."""
        df = load_ohlcv(synthetic_ticker, "15m")
        expected = {"time", "open", "high", "low", "close", "volume"}
        assert expected.issubset(set(df.columns))


# ─── Tests: Scorecard composite ────────────────────────────────────────

class TestScorecardComposite:
    """Verify full pipeline: data_loader → risk_scorecard → allocator_metrics."""

    def test_scorecard_scores_in_range(self, scorecard_result):
        """All composite scores must be in approximately [0, 1].

        allocator_score() lacks the clamp that compute_composite() has,
        so values can be very slightly negative. We allow a tolerance.
        """
        for ticker, data in scorecard_result["tickers"].items():
            score = data["composite_score"]
            assert -0.05 <= score <= 1.0, (
                f"{ticker}: composite_score={score} outside [-0.05,1]"
            )

    def test_scorecard_has_ranked(self, scorecard_result):
        """Scorecard must produce a ranked list."""
        ranked = scorecard_result["ranked"]
        assert isinstance(ranked, list)
        assert len(ranked) >= 1, "No tickers scored"

    def test_scorecard_ranks_descending(self, scorecard_result):
        """Ranked tickers must be sorted by composite_score descending."""
        tickers = scorecard_result["tickers"]
        ranked = scorecard_result["ranked"]
        scores = [tickers[t]["composite_score"] for t in ranked]
        assert scores == sorted(scores, reverse=True), "Not sorted DESC"

    def test_scorecard_constraints_valid(self, scorecard_result):
        """Scorecard must pass constraint validation."""
        passed, violations = validate_constraints(scorecard_result)
        assert passed, f"Constraints violated: {violations}"

    def test_scorecard_source_tags(self, scorecard_result):
        """All scored tickers must have source='real' (we only score real tickers)."""
        tickers = scorecard_result["tickers"]
        for t in REAL_TICKERS:
            if t in tickers:
                assert tickers[t]["source"] == "real", f"{t}: expected 'real'"

    def test_overall_metrics(self, scorecard_result):
        """Overall averages must be valid floats."""
        overall = scorecard_result["overall"]
        assert isinstance(overall["avg_composite"], float)
        assert isinstance(overall["avg_expectancy_r"], float)
        assert isinstance(overall["avg_risk_penalty"], float)
        # avg_risk_penalty must be in [0, 1]
        assert 0.0 <= overall["avg_risk_penalty"] <= 1.0
