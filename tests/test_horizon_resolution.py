"""Tests for 23G Canonical Horizon Resolution Layer.

Focused on:
  - Deterministic 90/180 slicing
  - Exact horizon mapping (60, 365)
  - 1095 insufficient coverage failure
  - No future leakage
  - Hash audit
  - No synthetic fallback for qualification
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

# ─── AST-guard: no broker imports ─────────────────────────────────────

def _assert_no_broker():
    source = inspect.getsource(inspect.getmodule(_assert_no_broker))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.lower()
                assert "tinkoff" not in name, f"Broker import: {alias.name}"
                assert "alpaca" not in name, f"Broker import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.lower()
                assert "tinkoff" not in mod, f"Broker import: {node.module}"
                assert "alpaca" not in mod, f"Broker import: {node.module}"

_assert_no_broker()

# ─── Path setup ───────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = str(_PROJECT_ROOT / "core")
_CODE_DIR = str(_PROJECT_ROOT / "code")
for d in [_CORE_DIR, _CODE_DIR]:
    if d not in sys.path:
        sys.path.insert(0, d)

from horizon_resolution import (
    DEFAULT_DATA_DIR,
    CSV_COLUMNS,
    InsufficientCoverageError,
    NoSourceFileError,
    ResolutionStrategy,
    resolve_horizon,
    resolve_universe,
)

DATA_DIR = DEFAULT_DATA_DIR

# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture
def ticker_gazp() -> str:
    return "GAZP"


@pytest.fixture
def ticker_sber() -> str:
    return "SBER"


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    return tmp_path


# ═══════════════════════════════════════════════════════════════════════
# EXACT HORIZON MAPPING
# ═══════════════════════════════════════════════════════════════════════


class TestExactHorizonMapping:
    """60d → exact 60d CSV; 365d → exact 365d CSV."""

    def test_60d_exact_file(self, data_dir: Path, ticker_gazp: str):
        """60-day request resolves from the exact 60d CSV file."""
        res = resolve_horizon(ticker_gazp, "15m", 60, data_dir)
        assert res.strategy == ResolutionStrategy.EXACT_FILE
        assert "60d" in res.source_path.name
        assert res.source_label_days == 60
        assert res.actual_coverage_days >= 55, (
            f"60d CSV should cover ≥55 days, got {res.actual_coverage_days}"
        )
        assert len(res.df) > 0
        assert list(res.df.columns) == CSV_COLUMNS

    def test_365d_exact_file(self, data_dir: Path, ticker_sber: str):
        """365-day request resolves from the exact 365d CSV file."""
        res = resolve_horizon(ticker_sber, "15m", 365, data_dir)
        assert res.strategy == ResolutionStrategy.EXACT_FILE
        assert "365d" in res.source_path.name
        assert res.source_label_days == 365
        assert res.actual_coverage_days >= 350, (
            f"365d CSV should cover ≥350 days, got {res.actual_coverage_days}"
        )

    def test_hash_populated(self, data_dir: Path, ticker_gazp: str):
        """Every resolution carries a non-empty SHA-256 hash."""
        res = resolve_horizon(ticker_gazp, "15m", 60, data_dir)
        assert len(res.file_hash_sha256) == 64, "Hash should be 64 hex chars"
        # Verify it matches actual file
        expected = hashlib.sha256(res.source_path.read_bytes()).hexdigest()
        assert res.file_hash_sha256 == expected

    def test_summary_dict(self, data_dir: Path, ticker_gazp: str):
        """summary_dict() returns all required keys."""
        res = resolve_horizon(ticker_gazp, "15m", 60, data_dir)
        s = res.summary_dict()
        assert "ticker" in s
        assert "file_hash_sha256" in s
        assert "actual_start" in s
        assert "actual_end" in s
        assert s["ticker"] == "GAZP"


# ═══════════════════════════════════════════════════════════════════════
# DETERMINISTIC 90/180 SLICING
# ═══════════════════════════════════════════════════════════════════════


class TestDeterministicSlicing:
    """90/180 horizons are deterministic tail-slices from 365d CSVs."""

    def test_90d_slice_from_365(self, data_dir: Path, ticker_gazp: str):
        """90-day request produces a deterministic tail-slice from 365d."""
        res = resolve_horizon(ticker_gazp, "15m", 90, data_dir)
        assert res.strategy == ResolutionStrategy.DETERMINISTIC_SLICE
        assert "365d" in res.source_path.name
        assert res.slice_start is not None
        # Slice should contain roughly 90 days of data
        slice_span = (res.df["time"].max() - res.df["time"].min()).days
        assert 85 <= slice_span <= 95, (
            f"90d slice should span ~90 days, got {slice_span}"
        )
        assert len(res.df) > 0

    def test_180d_slice_from_365(self, data_dir: Path, ticker_gazp: str):
        """180-day request produces a deterministic tail-slice from 365d."""
        res = resolve_horizon(ticker_gazp, "15m", 180, data_dir)
        assert res.strategy == ResolutionStrategy.DETERMINISTIC_SLICE
        assert "365d" in res.source_path.name
        slice_span = (res.df["time"].max() - res.df["time"].min()).days
        assert 175 <= slice_span <= 185, (
            f"180d slice should span ~180 days, got {slice_span}"
        )

    def test_90d_deterministic_same_result(self, data_dir: Path, ticker_gazp: str):
        """Calling 90d twice produces identical DataFrames (deterministic)."""
        r1 = resolve_horizon(ticker_gazp, "15m", 90, data_dir)
        r2 = resolve_horizon(ticker_gazp, "15m", 90, data_dir)
        pd.testing.assert_frame_equal(r1.df, r2.df)
        assert r1.file_hash_sha256 == r2.file_hash_sha256

    def test_180d_subset_of_365(self, data_dir: Path, ticker_gazp: str):
        """180d slice is a temporal subset of the 365d file."""
        r180 = resolve_horizon(ticker_gazp, "15m", 180, data_dir)
        r365 = resolve_horizon(ticker_gazp, "15m", 365, data_dir)
        # All 180d timestamps should be within 365d range
        assert r180.df["time"].min() >= r365.df["time"].min()
        assert r180.df["time"].max() <= r365.df["time"].max()
        assert len(r180.df) < len(r365.df)

    def test_90d_different_from_180d(self, data_dir: Path, ticker_gazp: str):
        """90d and 180d produce different slices."""
        r90 = resolve_horizon(ticker_gazp, "15m", 90, data_dir)
        r180 = resolve_horizon(ticker_gazp, "15m", 180, data_dir)
        assert len(r90.df) != len(r180.df)
        assert r90.slice_start != r180.slice_start


# ═══════════════════════════════════════════════════════════════════════
# 1095 INSUFFICIENT COVERAGE
# ═══════════════════════════════════════════════════════════════════════


class TestInsufficientCoverage:
    """1095d files have ~696 days actual → must fail closed."""

    def test_1095_fails_closed_gazp(self, data_dir: Path):
        """GAZP 1095d file has ~696 days → InsufficientCoverageError."""
        with pytest.raises(InsufficientCoverageError, match="696|FAIL CLOSED"):
            resolve_horizon("GAZP", "15m", 1095, data_dir)

    def test_1095_fails_closed_sber(self, data_dir: Path):
        """SBER 1095d file has ~696 days → InsufficientCoverageError."""
        with pytest.raises(InsufficientCoverageError, match="FAIL CLOSED"):
            resolve_horizon("SBER", "15m", 1095, data_dir)

    def test_1095_no_fallback_to_365(self, data_dir: Path, ticker_gazp: str):
        """1095 request does NOT fall back to 365d file."""
        with pytest.raises(InsufficientCoverageError):
            resolve_horizon(ticker_gazp, "15m", 1095, data_dir)
        # Verify 365d alone would work (to prove the test is meaningful)
        res365 = resolve_horizon(ticker_gazp, "15m", 365, data_dir)
        assert res365.df is not None and len(res365.df) > 0

    def test_1095_no_synthetic_fallback(self, empty_dir: Path):
        """1095 request with no CSV at all → NoSourceFileError (not synthetic)."""
        with pytest.raises(NoSourceFileError):
            resolve_horizon("FAKE", "15m", 1095, empty_dir)

    def test_known_short_1095d_files(self, data_dir: Path):
        """Audit: GAZP/SBER 1095d files have actual coverage < 1095 days.
        This confirms the filename-label mismatch that 1095 fail-closed guards against."""
        for ticker in ("GAZP", "SBER"):
            fp = data_dir / f"{ticker}_1095d_15m_continuous.csv"
            assert fp.exists(), f"{fp} should exist"
            df = pd.read_csv(fp)
            df["time"] = pd.to_datetime(df["time"], utc=True, format="mixed")
            span = (df["time"].max() - df["time"].min()).days + 1
            assert span < 1095, (
                f"{fp.name}: actual coverage {span} days ≥ 1095 — "
                f"unexpected; check data"
            )

    def test_1095d_files_have_actual_coverage_field(self, data_dir: Path):
        """Resolution reports actual coverage, not filename-label coverage."""
        fp = data_dir / "GAZP_1095d_15m_continuous.csv"
        df = pd.read_csv(fp)
        df["time"] = pd.to_datetime(df["time"], utc=True, format="mixed")
        actual = (df["time"].max() - df["time"].min()).days + 1
        # Filename says 1095 but actual is much less
        assert actual < 1095, f"Expected actual < 1095, got {actual}"
        assert actual > 500, f"Expected actual > 500, got {actual}"


# ═══════════════════════════════════════════════════════════════════════
# NO FUTURE LEAKAGE
# ═══════════════════════════════════════════════════════════════════════


class TestNoFutureLeakage:
    """as_of parameter clips data; no timestamps beyond as_of."""

    def test_90d_with_as_of(self, data_dir: Path, ticker_gazp: str):
        """90d with as_of=2026-06-01 → no data after 2026-06-01."""
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        res = resolve_horizon(ticker_gazp, "15m", 90, data_dir, as_of=as_of)
        assert res.df is not None
        assert len(res.df) > 0, "Should have data before as_of"
        assert res.df["time"].max() <= as_of, (
            f"Future leakage: max time {res.df['time'].max()} > as_of {as_of}"
        )

    def test_60d_with_as_of(self, data_dir: Path, ticker_gazp: str):
        """60d with as_of → clipped correctly."""
        as_of = datetime(2026, 8, 1, tzinfo=timezone.utc)
        res = resolve_horizon(ticker_gazp, "15m", 60, data_dir, as_of=as_of)
        assert res.df["time"].max() <= as_of

    def test_365d_with_as_of_clips(self, data_dir: Path, ticker_sber: str):
        """365d exact file with as_of → all rows <= as_of."""
        as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
        res = resolve_horizon(ticker_sber, "15m", 365, data_dir, as_of=as_of)
        assert res.df["time"].max() <= as_of
        # Should still have substantial data
        assert len(res.df) > 1000, f"Expected >1000 rows after clip, got {len(res.df)}"

    def test_as_of_none_no_clip(self, data_dir: Path, ticker_gazp: str):
        """as_of=None returns unclipped data."""
        r1 = resolve_horizon(ticker_gazp, "15m", 60, data_dir, as_of=None)
        assert r1.df is not None
        assert len(r1.df) > 0


# ═══════════════════════════════════════════════════════════════════════
# EDGE CASES & VALIDATION
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_invalid_horizon_raises(self):
        """horizon_days not in VALID_HORIZONS → ValueError."""
        with pytest.raises(ValueError, match="not in VALID_HORIZONS"):
            resolve_horizon("SBER", "15m", 500, DEFAULT_DATA_DIR)

    def test_invalid_interval_raises(self):
        """Invalid interval → ValueError."""
        with pytest.raises(ValueError, match="not valid"):
            resolve_horizon("SBER", "5m", 60, DEFAULT_DATA_DIR)

    def test_no_csv_for_ticker(self, empty_dir: Path):
        """Missing CSV → NoSourceFileError."""
        with pytest.raises(NoSourceFileError):
            resolve_horizon("NONEXIST", "15m", 60, empty_dir)

    def test_1h_interval_works(self, data_dir: Path, ticker_gazp: str):
        """1h interval resolves correctly."""
        res = resolve_horizon(ticker_gazp, "1h", 60, data_dir)
        assert res.interval == "1h"
        assert len(res.df) > 0
        assert list(res.df.columns) == CSV_COLUMNS


# ═══════════════════════════════════════════════════════════════════════
# UNIVERSE BATCH RESOLVE
# ═══════════════════════════════════════════════════════════════════════


class TestUniverseResolve:
    def test_resolve_universe_60d(self, data_dir: Path):
        """Batch resolve 60d for multiple tickers."""
        tickers = ["GAZP", "SBER", "LKOH"]
        results = resolve_universe(tickers, "15m", 60, data_dir)
        assert len(results) == 3
        for t in tickers:
            assert t in results
            assert results[t].df is not None and len(results[t].df) > 0

    def test_resolve_universe_1095d_skips(self, data_dir: Path):
        """1095d batch resolve skips tickers that fail closed."""
        tickers = ["GAZP", "SBER"]
        results = resolve_universe(tickers, "15m", 1095, data_dir)
        # Both should be skipped due to InsufficientCoverageError
        assert len(results) == 0
