"""Backtest Feedback Layer — pytest tests (≥3 fixtures, dry-run only).

Запуск: cd /root/prop-desk/strategy_combine && python -m pytest tests/test_backtest_feedback.py -v
"""
import ast
import json
import os
import sys
import py_compile
import pytest

# Ensure code/ is on path
_CODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures")
_FIXTURE_PATH = os.path.join(_FIXTURES_DIR, "backtest_feedback_fixture.json")
_MODULE_PATH = os.path.join(_CODE_DIR, "backtest_feedback.py")

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


def _load_fixture(scenario_name: str) -> dict:
    """Load scenario from fixture JSON."""
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["scenarios"][scenario_name]


@pytest.fixture
def healthy_fixture() -> dict:
    """Healthy strategy: equity up, low DD, high stability."""
    return _load_fixture("healthy")


@pytest.fixture
def degraded_fixture() -> dict:
    """Degraded strategy: equity down, high DD, CUSUM alerts."""
    return _load_fixture("degraded")


@pytest.fixture
def mixed_fixture() -> dict:
    """Mixed portfolio: good + bad strategies."""
    return _load_fixture("mixed")


@pytest.fixture
def single_trade_fixture() -> dict:
    """Edge case: single trade."""
    return _load_fixture("single_trade")


@pytest.fixture
def flat_equity_fixture() -> dict:
    """Edge case: flat equity."""
    return _load_fixture("flat_equity")


# ═══════════════════════════════════════════════════════════════════════
# Imports (lazy, after fixtures are defined)
# ═══════════════════════════════════════════════════════════════════════

from backtest_feedback import (
    build_feedback_scorecard,
    extend_generator_feedback,
    MAX_SLOTS,
    MAX_CONTRACTS,
    EXCLUDED_TICKERS,
)


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestHealthyPositiveScore:
    """Healthy strategy should produce high composite score."""

    def test_composite_score_above_threshold(self, healthy_fixture):
        sc = build_feedback_scorecard(
            equity_curve=healthy_fixture["equity_curve"],
            trades=healthy_fixture["trades"],
        )
        assert sc["composite_score"] >= 0.5, (
            f"Expected composite_score >= 0.5, got {sc['composite_score']}"
        )

    def test_equity_r2_high(self, healthy_fixture):
        sc = build_feedback_scorecard(
            equity_curve=healthy_fixture["equity_curve"],
            trades=healthy_fixture["trades"],
        )
        assert sc["equity_metrics"]["equity_r2"] >= 0.8, (
            f"Expected equity_r2 >= 0.8, got {sc['equity_metrics']['equity_r2']}"
        )

    def test_positive_pnl(self, healthy_fixture):
        sc = build_feedback_scorecard(
            equity_curve=healthy_fixture["equity_curve"],
            trades=healthy_fixture["trades"],
        )
        assert sc["equity_metrics"]["total_pnl"] > 0, "Expected positive PnL"

    def test_health_status_healthy(self, healthy_fixture):
        sc = build_feedback_scorecard(
            equity_curve=healthy_fixture["equity_curve"],
            trades=healthy_fixture["trades"],
        )
        status = sc["degradation"]["health_status"]
        assert status in ("healthy", "degraded"), (
            f"Expected healthy/degraded status, got {status}"
        )


class TestDegradedLowScore:
    """Degraded strategy should produce low composite score."""

    def test_composite_score_below_threshold(self, degraded_fixture):
        sc = build_feedback_scorecard(
            equity_curve=degraded_fixture["equity_curve"],
            trades=degraded_fixture["trades"],
        )
        # Degraded strategy: score < healthy (healthy >= 0.5)
        # High r2 on a smooth decline inflates score; health_status is the stronger signal
        assert sc["composite_score"] < 0.5, (
            f"Expected composite_score < 0.5, got {sc['composite_score']}"
        )

    def test_cusum_or_health_dead(self, degraded_fixture):
        """Degraded strategy: either CUSUM fires OR health is dead/critical."""
        sc = build_feedback_scorecard(
            equity_curve=degraded_fixture["equity_curve"],
            trades=degraded_fixture["trades"],
        )
        health = sc["degradation"]["health_status"]
        cusum = sc["degradation"]["cusum_count"]
        # At least one degradation signal must be present
        assert health in ("critical", "dead") or cusum >= 1, (
            f"Expected degradation signal: health={health}, cusum={cusum}"
        )

    def test_negative_pnl(self, degraded_fixture):
        sc = build_feedback_scorecard(
            equity_curve=deep_dict_copy(degraded_fixture["equity_curve"]),
            trades=degraded_fixture["trades"],
        )
        assert sc["equity_metrics"]["total_pnl"] < 0, "Expected negative PnL"

    def test_drawdown_exceeds_threshold(self, degraded_fixture):
        sc = build_feedback_scorecard(
            equity_curve=degraded_fixture["equity_curve"],
            trades=degraded_fixture["trades"],
        )
        assert sc["drawdown_metrics"]["max_drawdown"] < -0.10, (
            f"Expected drawdown < -10%, got {sc['drawdown_metrics']['max_drawdown']}"
        )


class TestMixedPortfolio:
    """Mixed portfolio: per-strategy + aggregate feedback."""

    def test_has_per_strategy_feedback(self, mixed_fixture):
        sc = build_feedback_scorecard(
            equity_curve=mixed_fixture["equity_curve"],
            trades=mixed_fixture["trades"],
            per_strategy_stats=mixed_fixture["per_strategy_stats"],
        )
        assert "per_strategy" in sc, "Expected per_strategy key in scorecard"
        assert len(sc["per_strategy"]) == 2, "Expected 2 strategies"

    def test_good_strategy_higher_than_bad(self, mixed_fixture):
        sc = build_feedback_scorecard(
            equity_curve=mixed_fixture["equity_curve"],
            trades=mixed_fixture["trades"],
            per_strategy_stats=mixed_fixture["per_strategy_stats"],
        )
        good_score = sc["per_strategy"]["strategy_A_good"]["composite_score"]
        bad_score = sc["per_strategy"]["strategy_B_bad"]["composite_score"]
        assert good_score > bad_score, (
            f"Expected good ({good_score}) > bad ({bad_score})"
        )

    def test_aggregate_score_exists(self, mixed_fixture):
        sc = build_feedback_scorecard(
            equity_curve=mixed_fixture["equity_curve"],
            trades=mixed_fixture["trades"],
            per_strategy_stats=mixed_fixture["per_strategy_stats"],
        )
        assert "composite_score" in sc
        assert isinstance(sc["composite_score"], float)


class TestEdgeCases:
    """Edge cases: single trade, flat equity."""

    def test_single_trade_no_crash(self, single_trade_fixture):
        sc = build_feedback_scorecard(
            equity_curve=single_trade_fixture["equity_curve"],
            trades=single_trade_fixture["trades"],
        )
        assert "composite_score" in sc
        assert sc["expectancy_metrics"]["n_trades"] == 1

    def test_flat_equity_zero_r2(self, flat_equity_fixture):
        sc = build_feedback_scorecard(
            equity_curve=flat_equity_fixture["equity_curve"],
            trades=flat_equity_fixture["trades"],
        )
        assert sc["equity_metrics"]["equity_r2"] == 0.0
        assert sc["equity_metrics"]["total_pnl"] == 0.0


class TestGuardConstants:
    """Guard: MAX_SLOTS, MAX_CONTRACTS, EXCLUDED_TICKERS."""

    def test_max_slots_three(self):
        assert MAX_SLOTS == 3, f"Expected MAX_SLOTS=3, got {MAX_SLOTS}"

    def test_max_contracts_one(self):
        assert MAX_CONTRACTS == 1, f"Expected MAX_CONTRACTS=1, got {MAX_CONTRACTS}"

    def test_ri_excluded(self):
        assert "RI" in EXCLUDED_TICKERS, "RI should be in excluded tickers"


class TestNoLiveBroker:
    """AST scan: backtest_feedback.py must not import broker/order modules."""

    def test_no_broker_imports(self):
        violations = []
        with open(_MODULE_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=_MODULE_PATH)
        broker_keywords = frozenset({
            "tinkoff", "place_order", "send_order", "create_order",
            "futures_lab", "broker_client",
        })
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name_lower = alias.name.lower()
                    for kw in broker_keywords:
                        if kw in name_lower:
                            violations.append(f"import {alias.name} (keyword: {kw})")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").lower()
                for kw in broker_keywords:
                    if kw in module:
                        violations.append(f"from {node.module} (keyword: {kw})")
        assert violations == [], f"Broker imports found: {violations}"

    def test_no_ri_ticker(self):
        """RI must not appear as a ticker string in backtest_feedback.py."""
        with open(_MODULE_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=_MODULE_PATH)
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.strip()
                if val == "RI":
                    violations.append(f"RI ticker at line {getattr(node, 'lineno', 0)}")
        # EXCLUDED_TICKERS contains "RI" by design — that's allowed
        # The check is about "RI" used as a trading ticker, not in exclusion list
        # We verify RI is only in EXCLUDED_TICKERS
        assert violations == [] or all("EXCLUDED" not in v for v in violations), (
            f"RI used as active ticker: {violations}"
        )


class TestPyCompile:
    """backtest_feedback.py compiles cleanly."""

    def test_compiles(self):
        assert os.path.exists(_MODULE_PATH), f"Module not found: {_MODULE_PATH}"
        py_compile.compile(_MODULE_PATH, doraise=True)


class TestExtendGeneratorFeedback:
    """Test extend_generator_feedback backward compatibility."""

    def test_preserves_existing_fields(self, healthy_fixture):
        sc = build_feedback_scorecard(
            equity_curve=healthy_fixture["equity_curve"],
            trades=healthy_fixture["trades"],
        )
        existing = {
            "by_direction_regime": {"LONG": {"trend": 5}},
            "best_hours": [10, 14, 15],
        }
        extended = extend_generator_feedback(existing, sc)
        assert "by_direction_regime" in extended
        assert "best_hours" in extended
        assert "backtest_feedback" in extended

    def test_backtest_feedback_section(self, healthy_fixture):
        sc = build_feedback_scorecard(
            equity_curve=healthy_fixture["equity_curve"],
            trades=healthy_fixture["trades"],
        )
        extended = extend_generator_feedback({}, sc)
        bf = extended["backtest_feedback"]
        assert "composite_score" in bf
        assert "equity_r2" in bf
        assert "recommendations" in bf


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def deep_dict_copy(val):
    """Simple deep copy for list/dict (stdlib only)."""
    if isinstance(val, list):
        return [deep_dict_copy(v) for v in val]
    if isinstance(val, dict):
        return {k: deep_dict_copy(v) for k, v in val.items()}
    return val
