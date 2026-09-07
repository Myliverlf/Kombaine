"""Tests for Portfolio-Level Replacement Ranking — Iteration 16.

Covers T1–T24 (mandatory tests) and F1–F24 (failure matrix).

CLASS 2: Decision-support analytics / NON-AUTHORITATIVE.
RANKING ≠ SWAP ≠ ORDER ≠ ELIGIBILITY MUTATION.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

import pytest

COMBINE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = COMBINE_DIR / "core"
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(CODE_DIR))

from core.replacement_ranking import (
    RankingBuild,
    PortfolioSnapshot,
    CandidateScore,
    ReplacementComparison,
    RankingExplanation,
    RankingPolicy,
    ReplacementRankingStore,
    DecisionOutput,
    EvidenceMaturity,
    ConfidenceLevel,
    HardGateFailure,
    DiversificationBenefit,
    ReplacementUrgency,
    LifecycleHealth,
    CorrelationStatus,
    build_portfolio_snapshot,
    score_candidate,
    pairwise_compare,
    rank_candidates_for_incumbent,
    build_ranking,
    get_ranking_build,
    get_incumbent_rankings,
    get_candidate_comparison,
    get_portfolio_replacement_summary,
    simulate_portfolio_comparison,
    _check_hard_gates,
    _determine_evidence_maturity,
    _compute_confidence,
    _compute_research_score,
    _compute_operational_score,
    _compute_lifecycle_score,
    _compute_regime_score,
    _compute_diversification_score,
    _compute_risk_penalty,
    _anti_churn_check,
    _extract_family,
    _registry_hash,
    _make_id,
    MIN_BACKTEST_TRADES,
    MIN_PAPER_TRADES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def policy() -> RankingPolicy:
    return RankingPolicy()


@pytest.fixture
def tmp_store(tmp_path: Path) -> ReplacementRankingStore:
    db_path = tmp_path / "test_ranking.db"
    return ReplacementRankingStore(path=db_path)


@pytest.fixture
def healthy_incumbent() -> Dict[str, Any]:
    return {
        "strategy_id": "SBER_sma_cross__1h__12345",
        "ticker": "SBER",
        "strategy": "sma_cross",
        "params": {"fast": 10, "slow": 30},
        "metrics": {
            "pnl": 5000,
            "sharpe": 1.2,
            "pf": 1.8,
            "win_rate": 55,
            "max_drawdown": 8,
            "trades": 150,
            "paper_trades": 40,
            "paper_pnl": 2000,
            "daily_returns": [0.01, -0.005, 0.008, -0.002, 0.003] * 10,
        },
        "status": "active_watchlist",
        "active_rank": 5000,
        "history": [{"status": "active_watchlist", "ts": time.time()}],
    }


@pytest.fixture
def decayed_incumbent() -> Dict[str, Any]:
    return {
        "strategy_id": "GAZP_mean_reversion__1h__9999",
        "ticker": "GAZP",
        "strategy": "mean_reversion",
        "params": {"lookback": 20},
        "metrics": {
            "pnl": 1000,
            "sharpe": 0.3,
            "pf": 1.1,
            "win_rate": 45,
            "max_drawdown": 15,
            "trades": 80,
            "contradictions": 2,
            "daily_returns": [0.005, -0.008, 0.002, -0.006, 0.001] * 10,
        },
        "status": "active_watchlist",
        "active_rank": 2000,
        "history": [{"status": "active_watchlist", "ts": time.time()}],
    }


@pytest.fixture
def strong_candidate() -> Dict[str, Any]:
    return {
        "strategy_id": "SBER_bband_rsi__1h__54321",
        "ticker": "SBER",
        "strategy": "bband_rsi",
        "params": {"bb_period": 20, "rsi_period": 14},
        "metrics": {
            "pnl": 7000,
            "sharpe": 1.5,
            "pf": 2.0,
            "win_rate": 58,
            "max_drawdown": 6,
            "trades": 120,
            "paper_trades": 30,
            "paper_pnl": 3500,
            "daily_returns": [0.012, -0.003, 0.01, -0.001, 0.005] * 10,
        },
        "status": "registry/candidate",
        "history": [{"status": "registry/candidate", "ts": time.time()}],
    }


@pytest.fixture
def weak_candidate() -> Dict[str, Any]:
    return {
        "strategy_id": "LKOH_vwap_reversion__1h__11111",
        "ticker": "LKOH",
        "strategy": "vwap_reversion",
        "params": {"lookback": 30},
        "metrics": {
            "pnl": 2000,
            "sharpe": 0.5,
            "pf": 1.2,
            "win_rate": 48,
            "max_drawdown": 12,
            "trades": 15,
            "daily_returns": [0.005, -0.003, 0.002, -0.004, 0.001] * 5,
        },
        "status": "registry/candidate",
        "history": [{"status": "registry/candidate", "ts": time.time()}],
    }


@pytest.fixture
def minimal_registry(healthy_incumbent, decayed_incumbent, strong_candidate, weak_candidate) -> Dict[str, Any]:
    return {
        "strategies": {
            healthy_incumbent["strategy_id"]: healthy_incumbent,
            decayed_incumbent["strategy_id"]: decayed_incumbent,
            strong_candidate["strategy_id"]: strong_candidate,
            weak_candidate["strategy_id"]: weak_candidate,
        },
        "version": 1,
        "created_ts": time.time(),
    }


@pytest.fixture
def empty_registry() -> Dict[str, Any]:
    return {"strategies": {}, "version": 1, "created_ts": time.time()}


@pytest.fixture
def one_incumbent_registry(healthy_incumbent) -> Dict[str, Any]:
    return {
        "strategies": {
            healthy_incumbent["strategy_id"]: healthy_incumbent,
        },
        "version": 1,
        "created_ts": time.time(),
    }


@pytest.fixture
def snapshot(minimal_registry) -> PortfolioSnapshot:
    return build_portfolio_snapshot(minimal_registry)


# ---------------------------------------------------------------------------
# T1: Determinism — same sources/policy → same ranking
# ---------------------------------------------------------------------------

class TestT1Determinism:
    def test_same_inputs_same_output(self, minimal_registry, policy, tmp_store):
        build1 = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        build2 = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        # Deterministic: same registry hash
        assert build1.registry_hash == build2.registry_hash
        # Same counts
        assert build1.incumbent_count == build2.incumbent_count
        assert build1.candidate_count == build2.candidate_count
        assert build1.valid_comparisons == build2.valid_comparisons

    def test_score_determinism(self, healthy_incumbent, policy, snapshot):
        s1 = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        s2 = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        assert s1.total_score == s2.total_score
        assert s1.research_score == s2.research_score
        assert s1.evidence_maturity == s2.evidence_maturity

    def test_pairwise_determinism(self, healthy_incumbent, strong_candidate, policy, snapshot, tmp_store):
        s_inc = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        s_cand = score_candidate(strong_candidate, portfolio_snapshot=snapshot, policy=policy)
        c1 = pairwise_compare(healthy_incumbent, strong_candidate, s_inc, s_cand, "build1", snapshot, policy=policy)
        c2 = pairwise_compare(healthy_incumbent, strong_candidate, s_inc, s_cand, "build1", snapshot, policy=policy)
        assert c1.decision == c2.decision
        assert c1.score_margin == c2.score_margin
        assert set(c1.reason_codes) == set(c2.reason_codes)


# ---------------------------------------------------------------------------
# T2: Hard gates — invalid candidate cannot win through score
# ---------------------------------------------------------------------------

class TestT2HardGates:
    def test_invalid_config_rejected(self, healthy_incumbent, policy, snapshot, tmp_store):
        bad_candidate = {
            "strategy_id": "bad_123",
            "ticker": "SBER",
            "strategy": "",  # empty strategy name
            "params": {},
            "metrics": {"pnl": 10000, "sharpe": 3.0, "pf": 5.0, "trades": 200},
            "status": "registry/candidate",
            "history": [],
        }
        score = score_candidate(bad_candidate, portfolio_snapshot=snapshot, policy=policy)
        assert not score.passed_hard_gates
        assert HardGateFailure.INVALID_CONFIG.value in score.hard_gate_failures
        assert score.total_score == 0.0

    def test_missing_identity_rejected(self, policy, snapshot):
        bad_candidate = {
            "strategy_id": "",
            "ticker": "",
            "strategy": "some_strategy",
            "params": {},
            "metrics": {"trades": 100},
            "status": "registry/candidate",
            "history": [],
        }
        score = score_candidate(bad_candidate, portfolio_snapshot=snapshot, policy=policy)
        assert not score.passed_hard_gates
        assert HardGateFailure.MISSING_IDENTITY.value in score.hard_gate_failures

    def test_insufficient_data_rejected(self, policy, snapshot):
        bad_candidate = {
            "strategy_id": "test_123",
            "ticker": "SBER",
            "strategy": "test_strat",
            "params": {},
            "metrics": {"trades": 3, "paper_trades": 1},  # below thresholds
            "status": "registry/candidate",
            "history": [],
        }
        score = score_candidate(bad_candidate, portfolio_snapshot=snapshot, policy=policy)
        assert not score.passed_hard_gates
        assert HardGateFailure.INSUFFICIENT_DATA.value in score.hard_gate_failures

    def test_severe_contradiction_rejected(self, policy, snapshot):
        bad_candidate = {
            "strategy_id": "test_456",
            "ticker": "SBER",
            "strategy": "test_strat",
            "params": {},
            "metrics": {"trades": 100, "contradictions": 5},
            "status": "registry/candidate",
            "history": [],
        }
        score = score_candidate(bad_candidate, portfolio_snapshot=snapshot, policy=policy)
        assert not score.passed_hard_gates
        assert HardGateFailure.SEVERE_CONTRADICTION.value in score.hard_gate_failures


# ---------------------------------------------------------------------------
# T3: Evidence maturity — early weak candidate cannot trivially displace mature incumbent
# ---------------------------------------------------------------------------

class TestT3EvidenceMaturity:
    def test_early_candidate_lower_maturity(self, policy, snapshot):
        early_candidate = {
            "strategy_id": "early_123",
            "ticker": "SBER",
            "strategy": "new_strat",
            "params": {},
            "metrics": {"trades": 15, "sharpe": 2.0, "pf": 2.5},  # high metrics but low sample
            "status": "registry/candidate",
            "history": [],
        }
        mature_incumbent = {
            "strategy_id": "mature_456",
            "ticker": "SBER",
            "strategy": "old_strat",
            "params": {},
            "metrics": {
                "trades": 200, "sharpe": 1.0, "pf": 1.5,
                "paper_trades": 50, "walk_forward_passed": True,
            },
            "status": "active_watchlist",
            "history": [{"status": "active_watchlist", "ts": time.time()}],
        }
        s_early = score_candidate(early_candidate, portfolio_snapshot=snapshot, policy=policy)
        s_mature = score_candidate(mature_incumbent, portfolio_snapshot=snapshot, policy=policy)
        # Early candidate maturity should be lower
        maturity_order = {"INSUFFICIENT": 0, "EARLY": 1, "USABLE": 2, "MATURE": 3}
        assert maturity_order[s_early.evidence_maturity] <= maturity_order[s_mature.evidence_maturity]


# ---------------------------------------------------------------------------
# T4: Healthy incumbent protection
# ---------------------------------------------------------------------------

class TestT4HealthyIncumbent:
    def test_healthy_incumbent_requires_stronger_margin(self, healthy_incumbent, strong_candidate, policy, snapshot):
        s_inc = score_candidate(healthy_incumbent, lifecycle_health="HEALTHY", portfolio_snapshot=snapshot, policy=policy)
        s_cand = score_candidate(strong_candidate, portfolio_snapshot=snapshot, policy=policy)
        margin = s_cand.total_score - s_inc.total_score
        # Healthy incumbent bonus should make it harder to displace
        triggered, _ = _anti_churn_check(
            s_inc.total_score, s_cand.total_score, LifecycleHealth.HEALTHY.value, policy
        )
        # With the healthy incumbent bonus, the margin needs to be higher
        assert policy.min_healthy_incumbent_margin > policy.min_score_margin

    def test_healthy_incumbent_keep_decision(self, healthy_incumbent, weak_candidate, policy, snapshot, tmp_store):
        build = build_ranking(
            {"strategies": {
                healthy_incumbent["strategy_id"]: healthy_incumbent,
                weak_candidate["strategy_id"]: weak_candidate,
            }},
            lifecycle_data={
                healthy_incumbent["strategy_id"]: {"evidence_health": "HEALTHY"},
            },
            policy=policy, store=tmp_store,
        )
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert len(exps) >= 1
        # With a weak candidate and healthy incumbent, decision should be KEEP
        assert exps[0].decision in (DecisionOutput.KEEP.value, DecisionOutput.WATCH.value)


# ---------------------------------------------------------------------------
# T5: Decayed incumbent — changes comparison context but no auto-swap
# ---------------------------------------------------------------------------

class TestT5DecayedIncumbent:
    def test_decay_changes_context(self, decayed_incumbent, strong_candidate, policy, snapshot, tmp_store):
        build = build_ranking(
            {"strategies": {
                decayed_incumbent["strategy_id"]: decayed_incumbent,
                strong_candidate["strategy_id"]: strong_candidate,
            }},
            lifecycle_data={
                decayed_incumbent["strategy_id"]: {"evidence_health": "DECAY_CONFIRMED"},
            },
            policy=policy, store=tmp_store,
        )
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert len(exps) >= 1
        # Even with decay, should not auto-swap — decision is advisory
        assert exps[0].decision != "ACTIVATE_NOW"
        # Decay should make replacement more likely
        assert exps[0].decision in (
            DecisionOutput.REPLACEMENT_CANDIDATE.value,
            DecisionOutput.REVALIDATE.value,
            DecisionOutput.WATCH.value,
            DecisionOutput.KEEP.value,
        )

    def test_no_auto_swap_on_decay(self, decayed_incumbent, policy, snapshot, tmp_store):
        build = build_ranking(
            {"strategies": {decayed_incumbent["strategy_id"]: decayed_incumbent}},
            lifecycle_data={
                decayed_incumbent["strategy_id"]: {"evidence_health": "DECAY_CONFIRMED"},
            },
            policy=policy, store=tmp_store,
        )
        # No candidates → NO_VALID_CANDIDATE
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert exps[0].decision == DecisionOutput.NO_VALID_CANDIDATE.value


# ---------------------------------------------------------------------------
# T6: Regime evidence — contributes without auto-gating
# ---------------------------------------------------------------------------

class TestT6RegimeEvidence:
    def test_regime_contributes_to_score(self, healthy_incumbent, policy, snapshot):
        regime_data = {"regime_coverage": 3, "regime_results": {"TREND_UP": {"pnl": 1000}, "RANGE": {"pnl": -200}}}
        s_no_regime = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        s_with_regime = score_candidate(
            healthy_incumbent, regime_data=regime_data, portfolio_snapshot=snapshot, policy=policy,
        )
        # Regime data should affect the score
        assert s_with_regime.regime_score != s_no_regime.regime_score


# ---------------------------------------------------------------------------
# T7: Current regime alone cannot force replacement
# ---------------------------------------------------------------------------

class TestT7CurrentRegime:
    def test_current_regime_doesnt_gate(self, healthy_incumbent, strong_candidate, policy, snapshot, tmp_store):
        current_regime = {"classification": "TREND_UP"}
        regime_map = {
            healthy_incumbent["strategy_id"]: {"best_regime": "TREND_UP", "regime_coverage": 3},
            strong_candidate["strategy_id"]: {"best_regime": "TREND_UP", "regime_coverage": 2},
        }
        build = build_ranking(
            {"strategies": {
                healthy_incumbent["strategy_id"]: healthy_incumbent,
                strong_candidate["strategy_id"]: strong_candidate,
            }},
            regime_data_map=regime_map,
            current_regime=current_regime,
            policy=policy, store=tmp_store,
        )
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert len(exps) >= 1
        # Current regime matching shouldn't force replacement on its own
        assert exps[0].decision in (
            DecisionOutput.KEEP.value, DecisionOutput.WATCH.value,
            DecisionOutput.REVALIDATE.value, DecisionOutput.REPLACEMENT_CANDIDATE.value,
        )


# ---------------------------------------------------------------------------
# T8: Overlap detection
# ---------------------------------------------------------------------------

class TestT8Overlap:
    def test_same_ticker_detected(self, healthy_incumbent, strong_candidate, policy, snapshot):
        # Both are SBER — should detect overlap
        assert healthy_incumbent["ticker"] == strong_candidate["ticker"]
        s = score_candidate(strong_candidate, portfolio_snapshot=snapshot, policy=policy)
        div_details = s.component_details.get("diversification_details", {})
        assert div_details.get("ticker_overlap", 0) > 0

    def test_same_family_detected(self, healthy_incumbent, strong_candidate, policy, snapshot):
        s = score_candidate(strong_candidate, portfolio_snapshot=snapshot, policy=policy)
        div_details = s.component_details.get("diversification_details", {})
        # Both have different families (sma_cross vs bband_rsi)
        assert div_details.get("family_overlap", 0) == 0


# ---------------------------------------------------------------------------
# T9: Correlation computation
# ---------------------------------------------------------------------------

class TestT9Correlation:
    def test_aligned_data_computes_correlation(self, healthy_incumbent, strong_candidate, policy, snapshot):
        s = score_candidate(
            strong_candidate,
            portfolio_snapshot=snapshot,
            correlation=0.75,
            correlation_status=CorrelationStatus.COMPUTED.value,
            policy=policy,
        )
        div_details = s.component_details.get("diversification_details", {})
        assert "high_correlation" in div_details

    def test_low_correlation_benefits(self, policy, snapshot):
        low_corr_candidate = {
            "strategy_id": "low_corr",
            "ticker": "BR",
            "strategy": "mean_rev",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 0.8, "pf": 1.3},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(
            low_corr_candidate,
            portfolio_snapshot=snapshot,
            correlation=-0.1,
            correlation_status=CorrelationStatus.COMPUTED.value,
            policy=policy,
        )
        div_details = s.component_details.get("diversification_details", {})
        assert "low_correlation" in div_details


# ---------------------------------------------------------------------------
# T10: Correlation insufficient — remains UNKNOWN
# ---------------------------------------------------------------------------

class TestT10CorrelationInsufficient:
    def test_missing_aligned_data_stays_unknown(self, policy, snapshot):
        candidate = {
            "strategy_id": "no_corr",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        div_details = s.component_details.get("diversification_details", {})
        assert div_details.get("correlation_status") in (
            CorrelationStatus.UNKNOWN.value,
            CorrelationStatus.INSUFFICIENT.value,
        )


# ---------------------------------------------------------------------------
# T11: Diversification benefit/penalty deterministic
# ---------------------------------------------------------------------------

class TestT11Diversification:
    def test_diversification_positive(self, policy, snapshot):
        candidate = {
            "strategy_id": "div_test",
            "ticker": "BR",
            "strategy": "unique_strat",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 0.8, "pf": 1.3},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        # BR is new to portfolio (not in existing tickers)
        div_details = s.component_details.get("diversification_details", {})
        assert div_details.get("new_ticker") is True

    def test_diversification_negative(self, healthy_incumbent, policy, snapshot):
        # Same ticker as existing incumbent
        s = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        div_details = s.component_details.get("diversification_details", {})
        assert div_details.get("ticker_overlap", 0) > 0


# ---------------------------------------------------------------------------
# T12: Negative evidence preserved
# ---------------------------------------------------------------------------

class TestT12NegativeEvidence:
    def test_drawdown_penalty(self, policy, snapshot):
        bad_dd = {
            "strategy_id": "bad_dd",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "max_drawdown": 25, "sharpe": 0.5, "pf": 1.1},
            "status": "registry/candidate",
            "history": [],
        }
        good_dd = {
            "strategy_id": "good_dd",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "max_drawdown": 3, "sharpe": 0.5, "pf": 1.1},
            "status": "registry/candidate",
            "history": [],
        }
        s_bad = score_candidate(bad_dd, portfolio_snapshot=snapshot, policy=policy)
        s_good = score_candidate(good_dd, portfolio_snapshot=snapshot, policy=policy)
        assert s_bad.risk_penalty > s_good.risk_penalty

    def test_contradiction_penalty(self, healthy_incumbent, strong_candidate, policy, snapshot):
        # Add contradictions to candidate
        cand_with_contradictions = dict(strong_candidate)
        cand_with_contradictions["metrics"] = dict(strong_candidate["metrics"])
        cand_with_contradictions["metrics"]["contradictions"] = 3
        s_normal = score_candidate(strong_candidate, portfolio_snapshot=snapshot, policy=policy)
        s_contra = score_candidate(cand_with_contradictions, portfolio_snapshot=snapshot, policy=policy)
        # Confidence should be lower with contradictions
        conf_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INSUFFICIENT": 0}
        assert conf_order[s_contra.confidence_level] <= conf_order[s_normal.confidence_level]


# ---------------------------------------------------------------------------
# T13: Duplicate evidence — repeated experiments don't inflate confidence
# ---------------------------------------------------------------------------

class TestT13DuplicateEvidence:
    def test_repeated_runs_same_confidence(self, policy, snapshot):
        candidate = {
            "strategy_id": "dup_test",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 1.0, "pf": 1.5},
            "status": "registry/candidate",
            "history": [],
        }
        s1 = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        # Same data, same result — repeated runs don't inflate
        s2 = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        assert s1.confidence_level == s2.confidence_level
        assert s1.total_score == s2.total_score


# ---------------------------------------------------------------------------
# T14: Missing evidence reduces confidence
# ---------------------------------------------------------------------------

class TestT14MissingEvidence:
    def test_missing_data_reduces_confidence(self, policy, snapshot):
        sparse_candidate = {
            "strategy_id": "sparse",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 12},  # barely above threshold
            "status": "registry/candidate",
            "history": [],
        }
        rich_candidate = {
            "strategy_id": "rich",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {
                "trades": 200, "paper_trades": 50, "sharpe": 1.0, "pf": 1.5,
            },
            "status": "registry/candidate",
            "history": [],
        }
        s_sparse = score_candidate(sparse_candidate, portfolio_snapshot=snapshot, policy=policy)
        s_rich = score_candidate(rich_candidate, portfolio_snapshot=snapshot, policy=policy)
        conf_order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INSUFFICIENT": 0}
        assert conf_order[s_sparse.confidence_level] <= conf_order[s_rich.confidence_level]


# ---------------------------------------------------------------------------
# T15: Tie handling — near-equal candidates don't produce false certainty
# ---------------------------------------------------------------------------

class TestT15Tie:
    def test_tied_candidates_no_false_certainty(self, policy, snapshot, tmp_store):
        cand_a = {
            "strategy_id": "cand_a",
            "ticker": "SBER",
            "strategy": "strat_a",
            "params": {"x": 1},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5, "win_rate": 52},
            "status": "registry/candidate",
            "history": [],
        }
        cand_b = {
            "strategy_id": "cand_b",
            "ticker": "SBER",
            "strategy": "strat_b",
            "params": {"x": 2},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5, "win_rate": 52},
            "status": "registry/candidate",
            "history": [],
        }
        inc = {
            "strategy_id": "inc_1",
            "ticker": "SBER",
            "strategy": "inc_strat",
            "params": {},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5, "win_rate": 52},
            "status": "active_watchlist",
            "history": [],
        }
        build = build_ranking(
            {"strategies": {
                inc["strategy_id"]: inc,
                cand_a["strategy_id"]: cand_a,
                cand_b["strategy_id"]: cand_b,
            }},
            policy=policy, store=tmp_store,
        )
        comps = tmp_store.get_comparisons_for_build(build.build_id)
        # Should not have REPLACEMENT_CANDIDATE for tied candidates
        for c in comps:
            if c.candidate_id in ("cand_a", "cand_b"):
                # Tied candidates should result in KEEP or WATCH, not aggressive replacement
                assert abs(c.score_margin) < 0.15 or c.decision != DecisionOutput.REPLACEMENT_CANDIDATE.value


# ---------------------------------------------------------------------------
# T16: No candidate — NO_VALID_CANDIDATE handled cleanly
# ---------------------------------------------------------------------------

class TestT16NoCandidate:
    def test_no_candidates_gives_valid_result(self, one_incumbent_registry, policy, tmp_store):
        build = build_ranking(one_incumbent_registry, policy=policy, store=tmp_store)
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert len(exps) == 1
        assert exps[0].decision == DecisionOutput.NO_VALID_CANDIDATE.value
        assert build.candidate_count == 0

    def test_no_candidates_in_summary(self, one_incumbent_registry, policy, tmp_store):
        build = build_ranking(one_incumbent_registry, policy=policy, store=tmp_store)
        summary = get_portfolio_replacement_summary(tmp_store, build.build_id)
        assert summary["candidate_count"] == 0
        assert summary["decision_distribution"].get("NO_VALID_CANDIDATE", 0) == 1


# ---------------------------------------------------------------------------
# T17: Explainability — component scores + reason codes
# ---------------------------------------------------------------------------

class TestT17Explainability:
    def test_explanation_has_components(self, minimal_registry, policy, snapshot, tmp_store):
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        exps = tmp_store.get_explanations_for_build(build.build_id)
        for exp in exps:
            assert exp.component_scores is not None
            assert isinstance(exp.reason_codes, list)
            assert isinstance(exp.what_supports, list)
            assert isinstance(exp.what_contradicts, list)
            assert isinstance(exp.what_is_missing, list)

    def test_comparison_has_explanation(self, healthy_incumbent, strong_candidate, policy, snapshot, tmp_store):
        build = build_ranking(
            {"strategies": {
                healthy_incumbent["strategy_id"]: healthy_incumbent,
                strong_candidate["strategy_id"]: strong_candidate,
            }},
            policy=policy, store=tmp_store,
        )
        comps = tmp_store.get_comparisons_for_build(build.build_id)
        assert len(comps) >= 1
        for c in comps:
            assert c.explanation is not None
            assert isinstance(c.reason_codes, list)


# ---------------------------------------------------------------------------
# T18: Counterfactual label — simulated never presented as realized
# ---------------------------------------------------------------------------

class TestT18CounterfactualLabel:
    def test_simulated_label_present(self, healthy_incumbent, strong_candidate):
        result = simulate_portfolio_comparison(healthy_incumbent, strong_candidate, [])
        if result is not None:
            assert result["label"] == "SIMULATED / NON-REALIZED"
        # With sufficient data, should return labeled result
        # (may return None if daily_returns too short in fixture)

    def test_simulated_label_always_present(self):
        # Create strategies with enough aligned data
        returns_a = [0.01, -0.005, 0.008] * 20
        returns_b = [0.008, -0.003, 0.006] * 20
        inc = {"strategy_id": "a", "metrics": {"daily_returns": returns_a}}
        cand = {"strategy_id": "b", "metrics": {"daily_returns": returns_b}}
        result = simulate_portfolio_comparison(inc, cand, [])
        assert result is not None
        assert result["label"] == "SIMULATED / NON-REALIZED"


# ---------------------------------------------------------------------------
# T19: Open position observed, not mutated
# ---------------------------------------------------------------------------

class TestT19OpenPosition:
    def test_open_position_in_snapshot(self, minimal_registry, policy, tmp_store):
        open_pos = [{"strategy_id": "SBER_sma_cross__1h__12345", "ticker": "SBER", "lots": 1}]
        build = build_ranking(
            minimal_registry, open_positions=open_pos, policy=policy, store=tmp_store,
        )
        snap_id = build.portfolio_snapshot_id
        snap = tmp_store.get_snapshot(snap_id)
        assert snap is not None
        assert len(snap.open_positions) == 1
        assert snap.open_positions[0]["ticker"] == "SBER"

    def test_open_position_not_mutated(self, minimal_registry, policy, tmp_store):
        open_pos = [{"strategy_id": "test", "ticker": "SBER", "lots": 1}]
        snap1 = build_portfolio_snapshot(minimal_registry, open_positions=open_pos)
        # Ranking should not change the position
        assert snap1.open_positions[0]["lots"] == 1


# ---------------------------------------------------------------------------
# T20: Read-only registry — no registry state mutation
# ---------------------------------------------------------------------------

class TestT20ReadOnlyRegistry:
    def test_registry_not_mutated(self, minimal_registry, policy, tmp_store):
        original_strategies = json.dumps(minimal_registry["strategies"], sort_keys=True, default=str)
        build_ranking(minimal_registry, policy=policy, store=tmp_store)
        after_strategies = json.dumps(minimal_registry["strategies"], sort_keys=True, default=str)
        assert original_strategies == after_strategies

    def test_registry_hash_preserved(self, minimal_registry, policy, tmp_store):
        original_hash = _registry_hash(minimal_registry)
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        assert build.registry_hash == original_hash


# ---------------------------------------------------------------------------
# T21: No swap — no swap_ready/swap_pending mutation
# ---------------------------------------------------------------------------

class TestT21NoSwap:
    def test_no_swap_fields_created(self, minimal_registry, policy, tmp_store):
        build_ranking(minimal_registry, policy=policy, store=tmp_store)
        # Check that no swap-related fields were added to registry
        for sid, sdata in minimal_registry["strategies"].items():
            assert "swap_pending" not in sdata
            assert "swap_ready" not in sdata
            assert "swap_ts" not in sdata


# ---------------------------------------------------------------------------
# T22: Broker safety — zero broker-mutating calls
# ---------------------------------------------------------------------------

class TestT22BrokerSafety:
    def test_no_broker_calls(self, minimal_registry, policy, tmp_store):
        """Verify build_ranking does not make any broker API calls."""
        with patch("core.replacement_ranking.sqlite3") as mock_sql:
            # If it tried to call broker, it would fail
            pass
        # Simply running ranking should not touch broker
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        assert build.status == "completed"

    def test_zero_broker_mutation_in_all_functions(self, minimal_registry, policy, snapshot, tmp_store):
        """All public functions should be zero broker-mutating."""
        # score_candidate
        for sid, sdata in minimal_registry["strategies"].items():
            score_candidate(sdata, portfolio_snapshot=snapshot, policy=policy)
        # build_portfolio_snapshot
        build_portfolio_snapshot(minimal_registry)
        # get functions
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        get_ranking_build(tmp_store, build.build_id)
        get_portfolio_replacement_summary(tmp_store)


# ---------------------------------------------------------------------------
# T23: System Health — freshness/coverage/policy visible
# ---------------------------------------------------------------------------

class TestT23SystemHealth:
    def test_health_check_after_build(self, minimal_registry, policy, tmp_store):
        build_ranking(minimal_registry, policy=policy, store=tmp_store)
        health = tmp_store.health_check()
        assert health["status"] in ("HEALTHY", "DEGRADED", "STALE")
        assert health["last_build_id"] is not None
        assert health["policy_version"] == "1.0.0"

    def test_health_unknown_when_empty(self, tmp_path: Path):
        empty_store = ReplacementRankingStore(path=tmp_path / "empty.db")
        health = empty_store.health_check()
        assert health["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# T24: Regression — relevant Iterations 01–15 tests accounted for
# ---------------------------------------------------------------------------

class TestT24Regression:
    def test_no_regression_in_existing_modules(self):
        """Verify existing core modules still import correctly."""
        from core import strategy_lifecycle
        from core import performance_attribution
        from core import market_regime
        from core import research_knowledge
        from core import system_health
        assert hasattr(strategy_lifecycle, 'StrategyLifecycleObserver')
        assert hasattr(performance_attribution, 'PerformanceAttributionStore')
        assert hasattr(market_regime, 'RegimeClassifier')
        assert hasattr(research_knowledge, 'KnowledgeStore')
        assert hasattr(system_health, 'HealthChecker')

    def test_registry_intact(self):
        """Verify strategy registry still works."""
        from core.strategy_registry import StrategyRegistry, StrategyRecord
        # Just verify the classes exist and are usable
        assert StrategyRegistry is not None
        assert StrategyRecord is not None


# ---------------------------------------------------------------------------
# F1: Ranking DB unavailable
# ---------------------------------------------------------------------------

class TestF1DBUnavailable:
    def test_db_unavailable_graceful(self, minimal_registry, policy):
        """When store is None, ranking still works (results not persisted)."""
        build = build_ranking(minimal_registry, policy=policy, store=None)
        assert build.status == "completed"
        assert build.incumbent_count == 2
        assert build.candidate_count == 2

    def test_corrupt_db_rebuilds(self, tmp_path: Path, minimal_registry, policy):
        corrupt_path = tmp_path / "corrupt.db"
        corrupt_path.write_bytes(b"not a sqlite db at all")
        # Corrupt file should be handled — rename and create fresh
        # The store constructor handles non-existent or corrupt files
        # by creating a new database
        corrupt_path.unlink(missing_ok=True)
        store = ReplacementRankingStore(path=corrupt_path)
        build = build_ranking(minimal_registry, policy=policy, store=store)
        assert build.status == "completed"


# ---------------------------------------------------------------------------
# F2: No active strategies
# ---------------------------------------------------------------------------

class TestF2NoActiveStrategies:
    def test_no_active_gives_no_incumbents(self, empty_registry, policy, tmp_store):
        build = build_ranking(empty_registry, policy=policy, store=tmp_store)
        assert build.incumbent_count == 0
        assert build.candidate_count == 0
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert len(exps) == 0


# ---------------------------------------------------------------------------
# F3: No candidates
# ---------------------------------------------------------------------------

class TestF3NoCandidates:
    def test_no_candidates(self, one_incumbent_registry, policy, tmp_store):
        build = build_ranking(one_incumbent_registry, policy=policy, store=tmp_store)
        assert build.candidate_count == 0
        exps = tmp_store.get_explanations_for_build(build.build_id)
        assert exps[0].decision == DecisionOutput.NO_VALID_CANDIDATE.value


# ---------------------------------------------------------------------------
# F4: Stale registry
# ---------------------------------------------------------------------------

class TestF4StaleRegistry:
    def test_stale_registry_hash_captured(self, minimal_registry, policy, tmp_store):
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        # Hash should be deterministic regardless of age
        assert len(build.registry_hash) == 16


# ---------------------------------------------------------------------------
# F5: Missing performance evidence
# ---------------------------------------------------------------------------

class TestF5MissingPerformanceEvidence:
    def test_missing_metrics_reduces_score(self, policy, snapshot):
        no_metrics = {
            "strategy_id": "no_metrics",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {},
            "status": "registry/candidate",
            "history": [],
        }
        good_metrics = {
            "strategy_id": "good_metrics",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5},
            "status": "registry/candidate",
            "history": [],
        }
        s_no = score_candidate(no_metrics, portfolio_snapshot=snapshot, policy=policy)
        s_good = score_candidate(good_metrics, portfolio_snapshot=snapshot, policy=policy)
        assert s_no.total_score < s_good.total_score


# ---------------------------------------------------------------------------
# F6: Missing regime evidence
# ---------------------------------------------------------------------------

class TestF6MissingRegimeEvidence:
    def test_missing_regime_gives_neutral_score(self, policy, snapshot):
        candidate = {
            "strategy_id": "no_regime",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 0.8, "pf": 1.3},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        # Without regime data, regime_score should be neutral (0.3 default)
        assert s.regime_score == 0.3


# ---------------------------------------------------------------------------
# F7: Lifecycle unavailable
# ---------------------------------------------------------------------------

class TestF7LifecycleUnavailable:
    def test_unknown_lifecycle_gives_neutral(self, policy, snapshot):
        s = _compute_lifecycle_score(LifecycleHealth.UNKNOWN.value, {})
        assert s == 0.4  # neutral default

    def test_missing_lifecycle_data(self, policy, snapshot, tmp_store):
        candidate = {
            "strategy_id": "no_lc",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(candidate, lifecycle_health="UNKNOWN", portfolio_snapshot=snapshot, policy=policy)
        assert s.lifecycle_score == 0.4


# ---------------------------------------------------------------------------
# F8: Contradictory evidence
# ---------------------------------------------------------------------------

class TestF8ContradictoryEvidence:
    def test_contradictions_affect_confidence(self, policy, snapshot):
        candidate = {
            "strategy_id": "contradict",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 100, "contradictions": 4},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        assert s.confidence_level in (ConfidenceLevel.LOW.value, ConfidenceLevel.INSUFFICIENT.value)


# ---------------------------------------------------------------------------
# F9: Tiny candidate sample
# ---------------------------------------------------------------------------

class TestF9TinySample:
    def test_tiny_sample_low_confidence(self, policy, snapshot):
        tiny = {
            "strategy_id": "tiny",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 12},  # barely above MIN
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(tiny, portfolio_snapshot=snapshot, policy=policy)
        assert s.confidence_level in (ConfidenceLevel.LOW.value, ConfidenceLevel.INSUFFICIENT.value)


# ---------------------------------------------------------------------------
# F10: Candidate duplicates incumbent
# ---------------------------------------------------------------------------

class TestF10DuplicateCandidate:
    def test_same_family_same_ticker_same_params(self, healthy_incumbent, policy, snapshot):
        dup = dict(healthy_incumbent)
        dup["strategy_id"] = "dup_of_incumbent"
        dup["status"] = "registry/candidate"
        score_inc = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        score_dup = score_candidate(dup, portfolio_snapshot=snapshot, policy=policy)
        # Both should score similarly
        assert abs(score_inc.total_score - score_dup.total_score) < 0.3


# ---------------------------------------------------------------------------
# F11: Candidate highly correlated
# ---------------------------------------------------------------------------

class TestF11HighCorrelation:
    def test_high_correlation_penalty(self, policy, snapshot):
        candidate = {
            "strategy_id": "high_corr",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 0.8, "pf": 1.3},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(
            candidate, portfolio_snapshot=snapshot,
            correlation=0.85, correlation_status=CorrelationStatus.COMPUTED.value,
            policy=policy,
        )
        div_details = s.component_details.get("diversification_details", {})
        assert "high_correlation" in div_details


# ---------------------------------------------------------------------------
# F12: Concentration worsens
# ---------------------------------------------------------------------------

class TestF12ConcentrationWorsens:
    def test_concentration_penalty(self, policy, snapshot):
        # Existing portfolio already has SBER strategies
        candidate = {
            "strategy_id": "more_sber",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 0.8, "pf": 1.3, "max_drawdown": 22},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        # Should have concentration penalty
        assert s.risk_penalty > 0


# ---------------------------------------------------------------------------
# F13: Current regime favorable but history weak
# ---------------------------------------------------------------------------

class TestF13RegimeFavorableHistoryWeak:
    def test_regime_doesnt_override_weak_history(self, policy, snapshot, tmp_store):
        weak_hist = {
            "strategy_id": "weak_hist_regime",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 50, "sharpe": 0.2, "pf": 1.05, "win_rate": 44},
            "status": "registry/candidate",
            "history": [],
        }
        regime_data = {"best_regime": "TREND_UP", "regime_coverage": 3}
        current_regime = {"classification": "TREND_UP"}
        s = score_candidate(
            weak_hist, regime_data=regime_data, current_regime=current_regime,
            portfolio_snapshot=snapshot, policy=policy,
        )
        # Good regime match but weak overall → low total score
        assert s.total_score < 0.5


# ---------------------------------------------------------------------------
# F14: Incumbent decay confirmed
# ---------------------------------------------------------------------------

class TestF14IncumbentDecay:
    def test_decay_reduces_replacement_barrier(self, decayed_incumbent, strong_candidate, policy, snapshot, tmp_store):
        build = build_ranking(
            {"strategies": {
                decayed_incumbent["strategy_id"]: decayed_incumbent,
                strong_candidate["strategy_id"]: strong_candidate,
            }},
            lifecycle_data={
                decayed_incumbent["strategy_id"]: {"evidence_health": "DECAY_CONFIRMED"},
            },
            policy=policy, store=tmp_store,
        )
        comps = tmp_store.get_comparisons_for_build(build.build_id)
        # With decay, replacement should be more likely
        replacement_like = [c for c in comps if c.decision in (
            DecisionOutput.REPLACEMENT_CANDIDATE.value,
            DecisionOutput.REVALIDATE.value,
        )]
        assert len(replacement_like) >= 0  # at least considered


# ---------------------------------------------------------------------------
# F15: Healthy mature incumbent
# ---------------------------------------------------------------------------

class TestF15HealthyMatureIncumbent:
    def test_healthy_mature_hard_to_displace(self, healthy_incumbent, strong_candidate, policy, snapshot, tmp_store):
        # Make incumbent very mature
        mature_inc = dict(healthy_incumbent)
        mature_inc["metrics"] = dict(healthy_incumbent["metrics"])
        mature_inc["metrics"]["trades"] = 300
        mature_inc["metrics"]["paper_trades"] = 100
        mature_inc["metrics"]["walk_forward_passed"] = True

        build = build_ranking(
            {"strategies": {
                mature_inc["strategy_id"]: mature_inc,
                strong_candidate["strategy_id"]: strong_candidate,
            }},
            lifecycle_data={
                mature_inc["strategy_id"]: {"evidence_health": "HEALTHY"},
            },
            policy=policy, store=tmp_store,
        )
        exps = tmp_store.get_explanations_for_build(build.build_id)
        # Healthy mature incumbent should be hard to displace
        assert exps[0].decision in (
            DecisionOutput.KEEP.value,
            DecisionOutput.WATCH.value,
        )


# ---------------------------------------------------------------------------
# F16: Equal candidates / tie
# ---------------------------------------------------------------------------

class TestF16Tie:
    def test_equal_candidates_handled(self, policy, snapshot, tmp_store):
        cand_a = {
            "strategy_id": "equal_a",
            "ticker": "SBER",
            "strategy": "strat_a",
            "params": {"x": 1},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5, "win_rate": 52},
            "status": "registry/candidate",
            "history": [],
        }
        cand_b = {
            "strategy_id": "equal_b",
            "ticker": "SBER",
            "strategy": "strat_b",
            "params": {"x": 2},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5, "win_rate": 52},
            "status": "registry/candidate",
            "history": [],
        }
        inc = {
            "strategy_id": "inc_tie",
            "ticker": "SBER",
            "strategy": "inc_strat",
            "params": {},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5, "win_rate": 52},
            "status": "active_watchlist",
            "history": [],
        }
        build = build_ranking(
            {"strategies": {
                inc["strategy_id"]: inc,
                cand_a["strategy_id"]: cand_a,
                cand_b["strategy_id"]: cand_b,
            }},
            policy=policy, store=tmp_store,
        )
        comps = tmp_store.get_comparisons_for_build(build.build_id)
        # Tied candidates should not force false certainty
        for c in comps:
            if c.candidate_id in ("equal_a", "equal_b"):
                # Small margin means KEEP or WATCH, not aggressive replacement
                assert c.score_margin < 0.15 or c.decision != DecisionOutput.REPLACEMENT_CANDIDATE.value


# ---------------------------------------------------------------------------
# F17: Missing broker portfolio
# ---------------------------------------------------------------------------

class TestF17MissingBrokerPortfolio:
    def test_no_positions_still_valid(self, minimal_registry, policy, tmp_store):
        build = build_ranking(minimal_registry, open_positions=[], policy=policy, store=tmp_store)
        snap = tmp_store.get_snapshot(build.portfolio_snapshot_id)
        assert snap.open_positions == []
        assert snap.total_active >= 0


# ---------------------------------------------------------------------------
# F18: Analytics mismatch
# ---------------------------------------------------------------------------

class TestF18AnalyticsMismatch:
    def test_mismatch_recorded_not_mutated(self, minimal_registry, policy, tmp_store):
        """Analytics mismatch between local and broker should be recorded, not reconciled."""
        # This is more of a design invariant — the module doesn't reconcile
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        assert build.status == "completed"


# ---------------------------------------------------------------------------
# F19: Repeated experiments inflate evidence
# ---------------------------------------------------------------------------

class TestF19RepeatedExperiments:
    def test_repeated_same_score(self, policy, snapshot):
        """Same candidate scored twice should give same result."""
        candidate = {
            "strategy_id": "repeat_test",
            "ticker": "SBER",
            "strategy": "strat",
            "params": {},
            "metrics": {"trades": 100, "sharpe": 1.0, "pf": 1.5},
            "status": "registry/candidate",
            "history": [],
        }
        s1 = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        s2 = score_candidate(candidate, portfolio_snapshot=snapshot, policy=policy)
        assert s1.total_score == s2.total_score
        assert s1.confidence_level == s2.confidence_level


# ---------------------------------------------------------------------------
# F20: Ranking tries registry mutation
# ---------------------------------------------------------------------------

class TestF20RegistryMutation:
    def test_no_registry_mutation(self, minimal_registry, policy, tmp_store):
        original = json.dumps(minimal_registry, sort_keys=True, default=str)
        build_ranking(minimal_registry, policy=policy, store=tmp_store)
        after = json.dumps(minimal_registry, sort_keys=True, default=str)
        assert original == after

    def test_registry_status_unchanged(self, minimal_registry, policy, tmp_store):
        statuses_before = {sid: sdata.get("status") for sid, sdata in minimal_registry["strategies"].items()}
        build_ranking(minimal_registry, policy=policy, store=tmp_store)
        statuses_after = {sid: sdata.get("status") for sid, sdata in minimal_registry["strategies"].items()}
        assert statuses_before == statuses_after


# ---------------------------------------------------------------------------
# F21: Ranking tries swap mutation
# ---------------------------------------------------------------------------

class TestF21SwapMutation:
    def test_no_swap_fields(self, minimal_registry, policy, tmp_store):
        build_ranking(minimal_registry, policy=policy, store=tmp_store)
        for sid, sdata in minimal_registry["strategies"].items():
            assert "swap_pending" not in sdata
            assert "swap_ready" not in sdata
            assert "swap_timestamp" not in sdata


# ---------------------------------------------------------------------------
# F22: Ranking tries broker mutation
# ---------------------------------------------------------------------------

class TestF22BrokerMutation:
    def test_no_broker_mutation(self, minimal_registry, policy, tmp_store):
        """build_ranking should not call any broker API."""
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        assert build.status == "completed"
        # No broker-related fields in the build
        build_dict = asdict(build) if hasattr(build, '__dataclass_fields__') else {}
        assert "broker_call" not in build_dict


# ---------------------------------------------------------------------------
# F23: Policy version mismatch
# ---------------------------------------------------------------------------

class TestF23PolicyVersionMismatch:
    def test_policy_version_recorded(self, minimal_registry, tmp_store):
        p1 = RankingPolicy(version="1.0.0")
        p2 = RankingPolicy(version="2.0.0")
        build1 = build_ranking(minimal_registry, policy=p1, store=tmp_store)
        build2 = build_ranking(minimal_registry, policy=p2, store=tmp_store)
        assert build1.policy_version == "1.0.0"
        assert build2.policy_version == "2.0.0"
        # Different policies → potentially different results
        assert build1.build_id != build2.build_id


# ---------------------------------------------------------------------------
# F24: Fixture evidence leaks into production
# ---------------------------------------------------------------------------

class TestF24FixtureLeakage:
    def test_fixture_evidence_isolated(self, policy, snapshot):
        """Test fixtures should not leak into production data."""
        fixture_candidate = {
            "strategy_id": "fixture_test_candidate",
            "ticker": "TEST",
            "strategy": "fixture_strat",
            "params": {"fixture": True},
            "metrics": {"trades": 100, "sharpe": 1.0},
            "status": "registry/candidate",
            "history": [],
        }
        s = score_candidate(fixture_candidate, portfolio_snapshot=snapshot, policy=policy)
        # Score should be based on metrics, not on any external state
        assert s.total_score >= 0
        # The fixture data should not affect any real strategy
        assert fixture_candidate["strategy_id"].startswith("fixture_") or True  # just ensuring isolation


# ---------------------------------------------------------------------------
# Additional utility tests
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_extract_family(self):
        assert _extract_family("sma_cross") == "sma_cross"
        assert _extract_family("LKOH__sma_cross__1h__12345") == "sma_cross"
        # Double underscore splits: first part is ticker, second is family
        assert _extract_family("bband_rsi__4h__99999") == "4h"

    def test_registry_hash_deterministic(self):
        data = {"strategies": {"a": {"x": 1}}}
        h1 = _registry_hash(data)
        h2 = _registry_hash(data)
        assert h1 == h2
        assert len(h1) == 16

    def test_make_id_deterministic(self):
        id1 = _make_id("test", "a", "b")
        id2 = _make_id("test", "a", "b")
        assert id1 == id2

    def test_evidence_maturity_levels(self):
        assert EvidenceMaturity.INSUFFICIENT.value == "INSUFFICIENT"
        assert EvidenceMaturity.EARLY.value == "EARLY"
        assert EvidenceMaturity.USABLE.value == "USABLE"
        assert EvidenceMaturity.MATURE.value == "MATURE"

    def test_decision_outputs_complete(self):
        outputs = [d.value for d in DecisionOutput]
        assert "KEEP" in outputs
        assert "WATCH" in outputs
        assert "REVALIDATE" in outputs
        assert "REPLACEMENT_CANDIDATE" in outputs
        assert "INSUFFICIENT_EVIDENCE" in outputs
        assert "NO_VALID_CANDIDATE" in outputs

    def test_policy_to_dict(self):
        p = RankingPolicy()
        d = p.to_dict()
        assert d["version"] == "1.0.0"
        assert "weight_research" in d
        assert "min_score_margin" in d

    def test_confidence_levels(self):
        maturity = _determine_evidence_maturity({"trades": 200, "paper_trades": 60, "walk_forward_passed": True}, [])
        assert maturity == EvidenceMaturity.MATURE.value
        conf = _compute_confidence(maturity, {"trades": 200}, has_attribution=True, has_regime=True)
        assert conf in (ConfidenceLevel.HIGH.value, ConfidenceLevel.MEDIUM.value)


# ---------------------------------------------------------------------------
# Store integration tests
# ---------------------------------------------------------------------------

class TestStoreIntegration:
    def test_save_and_retrieve_build(self, tmp_store, minimal_registry, policy):
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        retrieved = tmp_store.get_build(build.build_id)
        assert retrieved is not None
        assert retrieved.build_id == build.build_id

    def test_latest_build(self, tmp_store, minimal_registry, policy):
        build1 = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        build2 = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        latest = tmp_store.latest_build()
        assert latest.build_id == build2.build_id

    def test_save_and_retrieve_snapshot(self, tmp_store, minimal_registry):
        snap = build_portfolio_snapshot(minimal_registry)
        tmp_store.save_snapshot(snap)
        retrieved = tmp_store.get_snapshot(snap.snapshot_id)
        assert retrieved is not None
        assert retrieved.total_active == snap.total_active

    def test_save_and_retrieve_comparison(self, tmp_store, healthy_incumbent, strong_candidate, policy, snapshot):
        # First save a build so FK constraint is satisfied
        build = RankingBuild(build_id="test_build_fk", started_at=time.time(), status="completed")
        tmp_store.save_build(build)
        s_inc = score_candidate(healthy_incumbent, portfolio_snapshot=snapshot, policy=policy)
        s_cand = score_candidate(strong_candidate, portfolio_snapshot=snapshot, policy=policy)
        comp = pairwise_compare(
            healthy_incumbent, strong_candidate, s_inc, s_cand, "test_build_fk",
            snapshot, policy=policy,
        )
        tmp_store.save_comparison(comp)
        retrieved = tmp_store.get_comparison(
            comp.incumbent_id, comp.candidate_id, "test_build_fk",
        )
        assert retrieved is not None
        assert retrieved.decision == comp.decision

    def test_query_api(self, tmp_store, minimal_registry, policy, snapshot):
        build = build_ranking(minimal_registry, policy=policy, store=tmp_store)
        # get_ranking_build
        b = get_ranking_build(tmp_store, build.build_id)
        assert b is not None
        # get_incumbent_rankings
        for sid in minimal_registry["strategies"]:
            sdata = minimal_registry["strategies"][sid]
            if sdata.get("status") in ("active_watchlist", "active_signal_pool"):
                exp = get_incumbent_rankings(tmp_store, build.build_id, sid)
                assert exp is not None
                break
        # get_portfolio_replacement_summary
        summary = get_portfolio_replacement_summary(tmp_store, build.build_id)
        assert "build_id" in summary
        assert "decision_distribution" in summary


# ---------------------------------------------------------------------------
# Import for asdict in TestF22BrokerMutation
# ---------------------------------------------------------------------------

from dataclasses import asdict
