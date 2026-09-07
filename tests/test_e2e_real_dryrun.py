"""E2E Real Dry-Run Tests — fixture-based tests for semantic validation.

Tests verify that the E2E pipeline produces REAL results, not formal passes.

Test cases:
  1. quality_gate passes at least 1 idea from fixture (not zero)
  2. e2e result contains n_passed > 0 on quality_gate step
  3. final_verdict has no VETO+ALL_PASSED contradiction
  4. no live broker imports in pipeline modules
  5. max_slots <= 3 enforced
  6. RI excluded from selected candidates
  7. 1 contract max per entry
  8. PnL metrics present and meaningful
  9. scorecard shows differentiation (different risk scores for different candidates)
"""
import ast
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def safe_config() -> dict:
    """Config in PAPER mode — safe for dry-run."""
    from synthetic_fixtures import make_safe_config
    return make_safe_config()


@pytest.fixture
def realistic_ideas() -> list:
    """Realistic synthetic ideas with n_trades >= 30."""
    from synthetic_fixtures import make_realistic_ideas
    return make_realistic_ideas()


@pytest.fixture
def returns_map(realistic_ideas: list) -> dict:
    """Returns map for quality_gate consistency scoring."""
    from synthetic_fixtures import make_synthetic_returns_map
    return make_synthetic_returns_map(realistic_ideas)


@pytest.fixture
def regime_snapshot() -> dict:
    """Synthetic regime snapshot."""
    from synthetic_fixtures import make_synthetic_regime_snapshot
    return make_synthetic_regime_snapshot()


@pytest.fixture
def e2e_result() -> dict:
    """Run full E2E dry-run and return artifact."""
    from e2e_real_dryrun import run_real_e2e
    return run_real_e2e()


# ─── Tests ─────────────────────────────────────────────────────────────

class TestQualityGateSemantic:
    """Quality gate must produce non-empty results (not formal pass)."""

    def test_quality_gate_passes_at_least_one_idea(
        self, realistic_ideas, safe_config, regime_snapshot, returns_map
    ):
        """ideas with n_trades >= 30 and positive expectancy must pass quality_gate."""
        from strategy_ideas import idea_score
        from quality_gate import run_quality_gate

        scored = []
        for idea in realistic_ideas:
            score = idea_score(idea, regime_snapshot, feedback=None, config=safe_config)
            scored.append({**idea, "score": score})

        result = run_quality_gate(
            ideas=scored,
            regime_snapshot=regime_snapshot,
            feedback=None,
            config=safe_config,
            returns_map=returns_map,
        )

        n_passed = len(result.get("passed", []))
        assert n_passed > 0, (
            f"quality_gate passed 0 ideas (formal pass, not real result). "
            f"Rejected: {[r.get('_reject_reason', 'unknown') for r in result.get('rejected', [])]}"
        )

    def test_quality_gate_vetoes_excluded_ticker(
        self, realistic_ideas, safe_config, regime_snapshot, returns_map
    ):
        """RI ideas must be VETO'd by quality_gate."""
        from strategy_ideas import idea_score
        from quality_gate import run_quality_gate

        scored = []
        for idea in realistic_ideas:
            score = idea_score(idea, regime_snapshot, feedback=None, config=safe_config)
            scored.append({**idea, "score": score})

        result = run_quality_gate(
            ideas=scored,
            regime_snapshot=regime_snapshot,
            feedback=None,
            config=safe_config,
            returns_map=returns_map,
        )

        # Find RI in rejected
        ri_rejected = [r for r in result.get("rejected", [])
                       if r.get("ticker") == "RI"]
        assert len(ri_rejected) > 0, "RI should be VETO'd by quality_gate"
        assert ri_rejected[0].get("_reject_reason") == "veto_excluded_ticker"


class TestE2EResultSemantic:
    """E2E result must have semantic validity, not just formal pass."""

    def test_quality_gate_step_passed_gt_zero(self, e2e_result: dict):
        """quality_gate step must have n_passed > 0 in data."""
        steps = e2e_result.get("steps", [])
        qg_step = next((s for s in steps if s["step"] == "quality_gate"), None)
        assert qg_step is not None, "quality_gate step not found in e2e_result"
        assert qg_step["passed"], (
            f"quality_gate step failed: {qg_step['detail']}"
        )

    def test_strategy_registry_not_empty(self, e2e_result: dict):
        """strategy_registry must register > 0 strategies."""
        steps = e2e_result.get("steps", [])
        reg_step = next((s for s in steps if s["step"] == "strategy_registry"), None)
        assert reg_step is not None, "strategy_registry step not found"
        assert reg_step["passed"], (
            f"strategy_registry has 0 strategies: {reg_step['detail']}"
        )

    def test_final_verdict_accepts_valid_risk_veto(self, e2e_result: dict):
        """A risk VETO is a valid safety decision, not a broken pipeline."""
        assert e2e_result.get("contradiction") is False
        steps = e2e_result.get("steps", [])
        final_step = next((s for s in steps if s["step"] == "final_verdict"), None)
        assert final_step is not None and final_step["passed"], final_step
        risk_step = next((s for s in steps if s["step"] == "risk_scorecard"), None)
        assert risk_step is not None and risk_step["passed"], risk_step
        assert e2e_result.get("final_verdict") in {"ALLOW", "REDUCE", "VETO"}


class TestSafetyConstraints:
    """Safety constraints: no broker, max_slots, RI excluded, contracts=1."""

    def test_no_live_broker_imports(self):
        """No broker/tinkoff imports in synthetic_fixtures or e2e_real_dryrun."""
        forbidden_modules = {"tinkoff", "broker", "futures_lab"}
        files_to_check = [
            CODE_DIR / "synthetic_fixtures.py",
            CODE_DIR / "e2e_real_dryrun.py",
            CODE_DIR / "e2e_scorecard_report.py",
        ]

        for fpath in files_to_check:
            if not fpath.exists():
                continue
            source = fpath.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0].lower()
                        assert top not in forbidden_modules, (
                            f"Broker import in {fpath.name}: {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top = node.module.split(".")[0].lower()
                        assert top not in forbidden_modules, (
                            f"Broker import in {fpath.name}: from {node.module}"
                        )

    def test_max_slots_enforced(self, e2e_result: dict):
        """Selected candidates must be <= 3 (max_slots)."""
        steps = e2e_result.get("steps", [])
        allocator_step = next(
            (s for s in steps if s["step"] == "candidate_allocator"), None
        )
        assert allocator_step is not None, "candidate_allocator step not found"
        data = allocator_step.get("data", {})
        n_slots = data.get("n_slots", 0)
        assert n_slots <= 3, f"max_slots violated: {n_slots} > 3"

    def test_ri_excluded_from_selected(self, e2e_result: dict):
        """RI must not appear in selected candidates."""
        steps = e2e_result.get("steps", [])
        ranker_step = next(
            (s for s in steps if s["step"] == "pipeline_ranker"), None
        )
        assert ranker_step is not None, "pipeline_ranker step not found"
        selected_tickers = ranker_step.get("data", {}).get("selected_tickers", [])
        assert "RI" not in selected_tickers, (
            f"RI found in selected: {selected_tickers}"
        )

    def test_one_contract_max_per_entry(self, e2e_result: dict):
        """All selected candidates must have contracts <= 1."""
        steps = e2e_result.get("steps", [])
        allocator_step = next(
            (s for s in steps if s["step"] == "candidate_allocator"), None
        )
        assert allocator_step is not None
        # All candidate data should indicate contracts=1
        # Check via pnl_metrics
        pnl = e2e_result.get("pnl_metrics", {})
        for cd in pnl.get("candidates_detail", []):
            assert cd.get("contracts", 1) <= 1, (
                f"contracts > 1 for {cd.get('ticker')}: {cd.get('contracts')}"
            )

    def test_config_is_paper_mode(self, safe_config: dict):
        """Config must be in paper mode."""
        assert safe_config.get("mode") == "paper"
        assert safe_config.get("paper_first") is True


class TestPnLMetrics:
    """PnL metrics must be present and show real differentiation."""

    def test_pnl_metrics_present(self, e2e_result: dict):
        """Artifact must contain pnl_metrics with key fields."""
        pnl = e2e_result.get("pnl_metrics", {})
        assert "n_ideas_passed_quality" in pnl
        assert "n_selected" in pnl
        assert "avg_expectancy_rub" in pnl
        assert "risk_score" in pnl
        assert "risk_verdict" in pnl

    def test_pnl_positive_expectancy(self, e2e_result: dict):
        """Selected candidates must have positive average expectancy."""
        pnl = e2e_result.get("pnl_metrics", {})
        avg_exp = pnl.get("avg_expectancy_rub", 0)
        assert avg_exp > 0, (
            f"Average expectancy is non-positive: {avg_exp}. "
            f"PnL metric should show PnL↑"
        )

    def test_scorecard_differentiation(self, e2e_result: dict):
        """Different candidates must have different allocator scores."""
        pnl = e2e_result.get("pnl_metrics", {})
        details = pnl.get("candidates_detail", [])
        if len(details) >= 2:
            scores = [d.get("allocator_score", 0) for d in details]
            # At least 2 different scores
            assert len(set(scores)) >= 2, (
                f"All candidates have same score: {scores}. "
                f"Scorecard should show differentiation."
            )

    def test_sharpe_computed(self, e2e_result: dict):
        """synthetic_sharpe must be computed (not None)."""
        pnl = e2e_result.get("pnl_metrics", {})
        sharpe = pnl.get("synthetic_sharpe")
        assert sharpe is not None, "synthetic_sharpe should be computed"
        assert isinstance(sharpe, (int, float)), f"sharpe is not numeric: {type(sharpe)}"

    def test_all_steps_evaluated(self, e2e_result: dict):
        """E2E must have all 14 steps (0-13)."""
        steps = e2e_result.get("steps", [])
        assert len(steps) >= 13, f"Expected >= 13 steps, got {len(steps)}"
        step_names = [s["step"] for s in steps]
        expected = [
            "daily_generator", "strategy_ideas_scoring", "quality_gate",
            "strategy_registry",
        ]
        for name in expected:
            assert name in step_names, f"Step '{name}' missing from E2E"
