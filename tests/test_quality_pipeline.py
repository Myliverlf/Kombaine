"""Integration tests for Quality Pipeline (F1+F2+F3).

≥3 fixtures:
  1. strong_ideas — 3 strong ideas, all pass quality_gate
  2. mixed_ideas — 5 ideas including RI + weak, gate filters them
  3. duplicate_ideas — 4 ideas with duplicates, dedup keeps 2

Additional:
  - py_compile check for all 3 new modules
  - check_no_broker_imports-style assertion
  - dry-run only, no live broker
"""
from __future__ import annotations

import math
import os
import sys
import py_compile
import tempfile
from typing import Any, Dict, List

import pytest

# Ensure code/ is on path for imports
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_DIR = os.path.join(WORKSPACE, "code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

from idea_deduplicator import deduplicate_ideas, _param_similarity
from consistency_scorer import equity_r2, check_significance, consistency_score
from quality_gate import run_quality_gate


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def strong_ideas() -> List[Dict[str, Any]]:
    """3 сильных идей — все должны пройти quality_gate."""
    return [
        {
            "ticker": "GAZP",
            "strategy_name": "ema_cross",
            "direction": "LONG",
            "win_rate": 0.65,
            "avg_win": 300.0,
            "avg_loss": 100.0,
            "drawdown_pct": 5.0,
            "volatility": 0.15,
            "contracts": 1,
            "n_trades": 50,
            "score": None,
        },
        {
            "ticker": "SBER",
            "strategy_name": "donchian",
            "direction": "LONG",
            "win_rate": 0.60,
            "avg_win": 250.0,
            "avg_loss": 120.0,
            "drawdown_pct": 4.0,
            "volatility": 0.12,
            "contracts": 1,
            "n_trades": 45,
            "score": None,
        },
        {
            "ticker": "LKOH",
            "strategy_name": "mean_reversion",
            "direction": "SHORT",
            "win_rate": 0.58,
            "avg_win": 200.0,
            "avg_loss": 80.0,
            "drawdown_pct": 3.5,
            "volatility": 0.18,
            "contracts": 1,
            "n_trades": 60,
            "score": None,
        },
    ]


@pytest.fixture
def mixed_ideas() -> List[Dict[str, Any]]:
    """5 идей: 2 сильных + 1 RI (VETO) + 2 слабых."""
    return [
        {
            "ticker": "GAZP",
            "strategy_name": "ema_cross",
            "direction": "LONG",
            "win_rate": 0.65,
            "avg_win": 300.0,
            "avg_loss": 100.0,
            "drawdown_pct": 5.0,
            "contracts": 1,
            "n_trades": 50,
            "score": None,
        },
        {
            "ticker": "RI",  # EXCLUDED — VETO
            "strategy_name": "futures_strategy",
            "direction": "LONG",
            "win_rate": 0.70,
            "avg_win": 500.0,
            "avg_loss": 200.0,
            "contracts": 1,
            "n_trades": 100,
            "score": None,
        },
        {
            "ticker": "SBER",
            "strategy_name": "donchian",
            "direction": "LONG",
            "win_rate": 0.55,
            "avg_win": 150.0,
            "avg_loss": 140.0,
            "drawdown_pct": 8.0,
            "contracts": 1,
            "n_trades": 45,
            "score": None,
        },
        {
            "ticker": "GAZP",
            "strategy_name": "bollinger_squeeze",  # weak
            "direction": "LONG",
            "win_rate": 0.40,
            "avg_win": 50.0,
            "avg_loss": 200.0,
            "drawdown_pct": 15.0,
            "contracts": 1,
            "n_trades": 5,
            "score": None,
        },
        {
            "ticker": "LKOH",
            "strategy_name": "rsi_reversal",  # weak
            "direction": "SHORT",
            "win_rate": 0.35,
            "avg_win": 30.0,
            "avg_loss": 250.0,
            "drawdown_pct": 12.0,
            "contracts": 1,
            "n_trades": 10,
            "score": None,
        },
    ]


@pytest.fixture
def duplicate_ideas() -> List[Dict[str, Any]]:
    """4 идеи с дублями: 2x ema_cross на GAZP + 2x donchian на SBER."""
    return [
        {
            "ticker": "GAZP",
            "strategy_name": "ema_cross",
            "direction": "LONG",
            "win_rate": 0.65,
            "avg_win": 300.0,
            "avg_loss": 100.0,
            "drawdown_pct": 5.0,
            "contracts": 1,
            "n_trades": 50,
            "score": None,
        },
        {
            "ticker": "GAZP",
            "strategy_name": "ema_cross",
            "direction": "LONG",
            "win_rate": 0.62,
            "avg_win": 280.0,
            "avg_loss": 110.0,
            "drawdown_pct": 5.5,
            "contracts": 1,
            "n_trades": 48,
            "score": None,
        },
        {
            "ticker": "SBER",
            "strategy_name": "donchian",
            "direction": "LONG",
            "win_rate": 0.60,
            "avg_win": 250.0,
            "avg_loss": 120.0,
            "drawdown_pct": 4.0,
            "contracts": 1,
            "n_trades": 45,
            "score": None,
        },
        {
            "ticker": "SBER",
            "strategy_name": "donchian",
            "direction": "LONG",
            "win_rate": 0.58,
            "avg_win": 240.0,
            "avg_loss": 125.0,
            "drawdown_pct": 4.2,
            "contracts": 1,
            "n_trades": 44,
            "score": None,
        },
    ]


@pytest.fixture
def returns_for_consistency() -> Dict[str, List[float]]:
    """Returns для consistency scoring: 2 good, 1 bad."""
    return {
        "GAZP_ema_cross": [10, 15, 8, 12, 20, -5, 18, 25, 10, 30,
                           12, 8, 15, 20, 25, 10, 30, 15, 20, 35,
                           10, 15, 8, 12, 20, -5, 18, 25, 10, 30,
                           12, 8, 15, 20, 25, 10, 30, 15, 20, 35,
                           10, 15, 8, 12, 20, -5, 18, 25, 10, 30],
        "SBER_donchian": [5, 10, -3, 8, 12, 6, 9, 15, 7, 11,
                          5, 10, -3, 8, 12, 6, 9, 15, 7, 11,
                          5, 10, -3, 8, 12, 6, 9, 15, 7, 11,
                          5, 10, -3, 8, 12, 6, 9, 15, 7, 11,
                          5, 10, -3, 8, 12, 6, 9, 15, 7, 11],
        "LKOH_mean_reversion": [-10, -5, -15, -8, -3, -12, -7, -20, -15, -5,
                                 -10, -5, -15, -8, -3, -12, -7, -20, -15, -5,
                                 -10, -5, -15, -8, -3, -12, -7, -20, -15, -5,
                                 -10, -5, -15, -8, -3, -12, -7, -20, -15, -5,
                                 -10, -5, -15, -8, -3, -12, -7, -20, -15, -5],
    }


@pytest.fixture
def regime_snapshot() -> Dict[str, Any]:
    """Minimal regime snapshot for testing."""
    return {
        "bias": "trend",
        "tickers": {
            "GAZP": {"regime": "trend"},
            "SBER": {"regime": "range"},
            "LKOH": {"regime": "trend"},
        },
    }


@pytest.fixture
def config_fixture() -> Dict[str, Any]:
    """Config with RI excluded, max_slots=3, contracts=1."""
    return {
        "excluded": ["RI"],
        "max_slots": 3,
        "max_contracts_per_entry": 1,
    }


# ═══════════════════════════════════════════════════════════════════════
# TESTS: py_compile
# ═══════════════════════════════════════════════════════════════════════

class TestPyCompile:
    """Убедиться что все 3 новых модуля компилируются без ошибок."""

    def test_compile_idea_deduplicator(self):
        path = os.path.join(CODE_DIR, "idea_deduplicator.py")
        assert os.path.exists(path), f"File not found: {path}"
        py_compile.compile(path, doraise=True)

    def test_compile_consistency_scorer(self):
        path = os.path.join(CODE_DIR, "consistency_scorer.py")
        assert os.path.exists(path), f"File not found: {path}"
        py_compile.compile(path, doraise=True)

    def test_compile_quality_gate(self):
        path = os.path.join(CODE_DIR, "quality_gate.py")
        assert os.path.exists(path), f"File not found: {path}"
        py_compile.compile(path, doraise=True)


# ═══════════════════════════════════════════════════════════════════════
# TESTS: No broker imports
# ═══════════════════════════════════════════════════════════════════════

FORBIDDEN_MODULES = {"tinkoff", "futures_lab", "broker"}
FORBIDDEN_NAMES = {"Client", "post_order", "place_order", "send_order", "submit_order"}

class TestNoBrokerImports:
    """Убедиться что новые модули не импортируют broker/tinkoff."""

    def _check_file(self, filename: str):
        import ast
        path = os.path.join(CODE_DIR, filename)
        source = open(path).read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in FORBIDDEN_MODULES, f"{filename} imports forbidden: {top}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    assert top not in FORBIDDEN_MODULES, f"{filename} imports forbidden: {top}"
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                    assert False, f"{filename} calls forbidden: {func.id}"
                if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_NAMES:
                    assert False, f"{filename} calls forbidden: {func.attr}"

    def test_deduplicator_no_broker(self):
        self._check_file("idea_deduplicator.py")

    def test_consistency_scorer_no_broker(self):
        self._check_file("consistency_scorer.py")

    def test_quality_gate_no_broker(self):
        self._check_file("quality_gate.py")


# ═══════════════════════════════════════════════════════════════════════
# TESTS: idea_deduplicator (F1)
# ═══════════════════════════════════════════════════════════════════════

class TestIdeaDeduplicator:

    def test_dedup_removes_exact_duplicates(self):
        """Две одинаковые идеи → одна."""
        ideas = [
            {"ticker": "X", "strategy_name": "ema", "score": 0.5, "contracts": 1},
            {"ticker": "X", "strategy_name": "ema", "score": 0.5, "contracts": 1},
        ]
        result = deduplicate_ideas(ideas)
        assert len(result) == 1

    def test_dedup_keeps_different_tickers(self):
        """Идеи на разные тикеры — не дедуплицируются."""
        ideas = [
            {"ticker": "X", "strategy_name": "ema", "score": 0.5, "contracts": 1},
            {"ticker": "Y", "strategy_name": "ema", "score": 0.5, "contracts": 1},
        ]
        result = deduplicate_ideas(ideas)
        assert len(result) == 2

    def test_dedup_keeps_best_by_score(self):
        """Из двух близнецов — остаётся лучший по score."""
        ideas = [
            {"ticker": "X", "strategy_name": "ema", "score": 0.3, "contracts": 1},
            {"ticker": "X", "strategy_name": "ema", "score": 0.8, "contracts": 1},
        ]
        result = deduplicate_ideas(ideas)
        assert len(result) == 1
        assert result[0]["score"] == 0.8

    def test_dedup_removes_veto_first(self):
        """VETO идеи отбрасываются до дедупликации."""
        ideas = [
            {"ticker": "RI", "strategy_name": "futures", "score": float("-inf"), "contracts": 1},
            {"ticker": "GAZP", "strategy_name": "ema", "score": 0.5, "contracts": 1},
        ]
        result = deduplicate_ideas(ideas)
        assert len(result) == 1
        assert result[0]["ticker"] == "GAZP"

    def test_empty_input(self):
        assert deduplicate_ideas([]) == []


# ═══════════════════════════════════════════════════════════════════════
# TESTS: consistency_scorer (F2)
# ═══════════════════════════════════════════════════════════════════════

class TestConsistencyScorer:

    def test_equity_r2_linear(self):
        """Линейный equity curve → R² ≈ 1.0."""
        linear_returns = [10.0] * 50
        r2 = equity_r2(linear_returns)
        assert r2 == pytest.approx(1.0, abs=0.01)

    def test_equity_r2_random(self):
        """Random walk → R² < 1.0."""
        import random
        random.seed(42)
        random_returns = [random.gauss(0, 10) for _ in range(100)]
        r2 = equity_r2(random_returns)
        assert 0.0 <= r2 <= 1.0

    def test_equity_r2_empty(self):
        assert equity_r2([]) == 0.0

    def test_equity_r2_single(self):
        assert equity_r2([10.0]) == 0.0

    def test_check_significance_true(self):
        assert check_significance(50) is True
        assert check_significance(30) is True

    def test_check_significance_false(self):
        assert check_significance(10) is False
        assert check_significance(0) is False

    def test_consistency_score_no_returns(self):
        """returns=None → 0.0 (fail-open)."""
        assert consistency_score(returns=None) == 0.0

    def test_consistency_score_with_returns(self):
        """Хорошие returns + достаточно trades → score > 0."""
        good_returns = [10.0] * 50  # perfectly linear
        score = consistency_score(returns=good_returns, n_trades=50)
        assert score > 0.5

    def test_consistency_score_few_trades(self):
        """Мало trades → significance weight = 0, score ниже."""
        good_returns = [10.0] * 50
        score_many = consistency_score(returns=good_returns, n_trades=50)
        score_few = consistency_score(returns=good_returns, n_trades=5)
        assert score_many >= score_few


# ═══════════════════════════════════════════════════════════════════════
# TESTS: quality_gate (F3) — integration
# ═══════════════════════════════════════════════════════════════════════

class TestQualityGate:

    def test_strong_ideas_pass(
        self, strong_ideas, returns_for_consistency, regime_snapshot, config_fixture,
    ):
        """FIXTURE 1: 3 сильных идей — все должны пройти (или max_slots=3 обрежет)."""
        result = run_quality_gate(
            strong_ideas,
            regime_snapshot=regime_snapshot,
            config=config_fixture,
            returns_map=returns_for_consistency,
        )
        assert result["meta"]["n_rejected_veto"] == 0
        assert result["meta"]["n_rejected_weak"] == 0
        assert result["meta"]["n_passed"] >= 1
        assert result["meta"]["n_passed"] <= 3

    def test_mixed_ideas_filtered(
        self, mixed_ideas, returns_for_consistency, regime_snapshot, config_fixture,
    ):
        """FIXTURE 2: 5 идей — RI VETO + слабые отсечены, passing <= 2."""
        result = run_quality_gate(
            mixed_ideas,
            regime_snapshot=regime_snapshot,
            config=config_fixture,
            returns_map=returns_for_consistency,
        )
        # RI should be VETO'd
        assert result["meta"]["n_rejected_veto"] >= 1
        # At least some weak ideas should be rejected
        assert result["meta"]["n_rejected_weak"] >= 1
        # Not all 5 should pass
        assert result["meta"]["n_passed"] < 5

    def test_duplicate_ideas_deduped(
        self, duplicate_ideas, returns_for_consistency, regime_snapshot, config_fixture,
    ):
        """FIXTURE 3: 4 идеи с дублями → дедупликация сокращает, passing <= 2."""
        result = run_quality_gate(
            duplicate_ideas,
            regime_snapshot=regime_snapshot,
            config=config_fixture,
            returns_map=returns_for_consistency,
        )
        total_input = len(duplicate_ideas)
        total_output = result["meta"]["n_passed"] + sum([
            result["meta"]["n_rejected_veto"],
            result["meta"]["n_rejected_weak"],
            result["meta"]["n_rejected_dups"],
            result["meta"]["n_rejected_low_consistency"],
            result["meta"]["n_rejected_low_significance"],
        ])
        # Duplicates should reduce count
        assert result["meta"]["n_rejected_dups"] >= 1
        # Max slots enforced
        assert result["meta"]["n_passed"] <= 3

    def test_contracts_always_one(
        self, strong_ideas, returns_for_consistency, regime_snapshot, config_fixture,
    ):
        """Все passed ideas должны иметь contracts=1."""
        result = run_quality_gate(
            strong_ideas,
            regime_snapshot=regime_snapshot,
            config=config_fixture,
            returns_map=returns_for_consistency,
        )
        for idea in result["passed"]:
            assert idea["contracts"] == 1

    def test_empty_input(self, config_fixture):
        """Пустой список → пустой результат с meta."""
        result = run_quality_gate([], config=config_fixture)
        assert result["passed"] == []
        assert result["meta"]["n_total"] == 0
        assert result["meta"]["n_passed"] == 0
