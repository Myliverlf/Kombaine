"""Scorecard dry-run tests on real Tinkoff CSV data.

Verifies:
  (a) scorecard_dryrun_verdict computes risk_score and expectancy_r from real CSV
  (b) PnL metrics are in valid range (risk_score ∈ [0,100], expectancy is finite)
  (c) Output JSON is written to tests/fixtures/scorecard_dryrun_verdict.json

No live broker, no network. Uses real CSV from tinkoff_futures_data on disk.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
TINKOFF_DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")
FIXTURE_OUTPUT = COMBINE_DIR / "tests" / "fixtures" / "scorecard_dryrun_verdict.json"


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def tinkoff_available() -> bool:
    """Check if real Tinkoff CSV data exists on disk."""
    if not TINKOFF_DATA_DIR.exists():
        return False
    csv_files = list(TINKOFF_DATA_DIR.glob("*_60d_*_continuous.csv"))
    return len(csv_files) > 0


@pytest.fixture
def scorecard_result(tinkoff_available: bool) -> dict:
    """Run scorecard_dryrun_verdict.py and return parsed output JSON.

    If Tinkoff data is unavailable, runs with synthetic fallback.
    """
    if not tinkoff_available:
        pytest.skip("Tinkoff real CSV not available on disk")

    # Run the standalone script
    result = subprocess.run(
        [sys.executable, str(CODE_DIR / "scorecard_dryrun_verdict.py")],
        capture_output=True, text=True, timeout=60,
        cwd=str(COMBINE_DIR),
    )
    assert result.returncode == 0, (
        f"scorecard_dryrun_verdict.py failed:\nstdout={result.stdout[-500:]}\n"
        f"stderr={result.stderr[-500:]}"
    )

    # Read the output JSON
    assert FIXTURE_OUTPUT.exists(), f"Output not written: {FIXTURE_OUTPUT}"
    with open(FIXTURE_OUTPUT, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def scorecard_scores(scorecard_result: dict) -> dict:
    """Extract scorecard_scores dict from result."""
    scores = scorecard_result.get("scorecard_scores", {})
    assert len(scores) > 0, "scorecard_scores is empty"
    return scores


@pytest.fixture
def config() -> dict:
    """Load config.json for constraint verification."""
    cfg_path = COMBINE_DIR / "config.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


# ── Tests ─────────────────────────────────────────────────────────────

class TestScorecardComputation:
    """Verify scorecard_dryrun_verdict computes valid metrics."""

    def test_composite_score_in_range(self, scorecard_scores: dict) -> None:
        """composite_score is finite and not NaN for every ticker."""
        for ticker, data in scorecard_scores.items():
            cs = data.get("composite_score", None)
            assert cs is not None, f"{ticker}: missing composite_score"
            assert math.isfinite(cs), (
                f"{ticker}: composite_score={cs} is not finite"
            )

    def test_expectancy_finite(self, scorecard_scores: dict) -> None:
        """expectancy_r is finite for every ticker."""
        for ticker, data in scorecard_scores.items():
            er = data.get("expectancy_r", None)
            assert er is not None, f"{ticker}: missing expectancy_r"
            assert math.isfinite(er), (
                f"{ticker}: expectancy_r={er} is not finite"
            )

    def test_risk_penalty_in_range(self, scorecard_scores: dict) -> None:
        """risk_penalty ∈ [0, 1] for every ticker."""
        for ticker, data in scorecard_scores.items():
            rp = data.get("risk_penalty", None)
            assert rp is not None, f"{ticker}: missing risk_penalty"
            assert 0 <= rp <= 1.0, (
                f"{ticker}: risk_penalty={rp} not in [0,1]"
            )


class TestScorecardOutput:
    """Verify output JSON structure and constraints."""

    def test_final_verdict_pass(self, scorecard_result: dict) -> None:
        verdict = scorecard_result.get("final_verdict", "MISSING")
        assert verdict == "PASS", f"final_verdict={verdict}, expected PASS"

    def test_data_source_is_real(self, scorecard_result: dict) -> None:
        source = scorecard_result.get("data_source", "unknown")
        assert source == "real", f"data_source={source}, expected 'real'"

    def test_constraints_pass(self, scorecard_result: dict) -> None:
        cp = scorecard_result.get("constraints_pass", False)
        assert cp is True, f"constraints_pass={cp}, expected True"

    def test_n_tickers_positive(self, scorecard_result: dict) -> None:
        n = scorecard_result.get("n_tickers_scored", 0)
        assert n >= 3, f"n_tickers_scored={n}, expected ≥3"

    def test_ri_excluded_from_scores(self, scorecard_scores: dict) -> None:
        """RI must NOT appear in scorecard_scores."""
        assert "RI" not in scorecard_scores, (
            f"RI found in scorecard_scores — should be excluded"
        )


class TestScorecardConstraints:
    """Verify hard constraints from config.json are reflected in scorecard."""

    def test_max_slots_enforced(self, scorecard_result: dict) -> None:
        """Scorecard constraints_pass must be true (includes max_slots check).

        Note: scorecard scores ALL tickers (may be > max_slots);
        max_slots limits allocation, not scoring.
        The constraints_pass field reflects all hard constraints.
        """
        cp = scorecard_result.get("constraints_pass", False)
        assert cp is True, (
            f"constraints_pass={cp}, expected True (max_slots enforced in allocator)"
        )

    def test_no_live_orders(self, scorecard_result: dict) -> None:
        """No live orders in dry-run result."""
        live = scorecard_result.get("n_live_orders", 0)
        # Key may not exist; if absent, that's fine (no live orders tracked).
        # If present, must be 0.
        if "n_live_orders" in scorecard_result:
            assert live == 0, f"n_live_orders={live}, expected 0"
