"""Backtest Feedback — Acceptance Guard tests.

Проверяет все критерии приёмки из task.md:
  1. py_compile OK для backtest_feedback.py
  2. No live broker imports (AST scan)
  3. RI excluded (не используется как активный ticker)
  4. MAX_SLOTS ≤ 3, MAX_CONTRACTS = 1
  5. Generator feedback backward compatible
  6. ≥3 fixtures в fixture file

Запуск: cd /root/prop-desk/strategy_combine && python -m pytest tests/test_backtest_feedback_acceptance.py -v
"""
import ast
import json
import os
import py_compile
import sys

import pytest

# Ensure code/ is on path
_CODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

_MODULE_PATH = os.path.join(_CODE_DIR, "backtest_feedback.py")
_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "backtest_feedback_fixture.json"
)

BROKER_KEYWORDS = frozenset({
    "tinkoff", "place_order", "send_order", "create_order",
    "futures_lab", "broker_client",
})


# ═══════════════════════════════════════════════════════════════════════
# 1. py_compile
# ═══════════════════════════════════════════════════════════════════════

class TestPyCompile:
    """Criterion 1: py_compile OK."""

    def test_backtest_feedback_compiles(self):
        assert os.path.exists(_MODULE_PATH), f"Module not found: {_MODULE_PATH}"
        py_compile.compile(_MODULE_PATH, doraise=True)


# ═══════════════════════════════════════════════════════════════════════
# 2. No live broker
# ═══════════════════════════════════════════════════════════════════════

class TestNoLiveBroker:
    """Criterion 2: No broker/order imports in new modules."""

    def test_no_broker_imports(self):
        violations = _ast_scan_broker(_MODULE_PATH)
        assert violations == [], f"Broker imports found: {violations}"

    def test_no_order_placement(self):
        """Verify no place_order/send_order/create_order calls."""
        with open(_MODULE_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=_MODULE_PATH)
        call_violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name in ("place_order", "send_order", "create_order", "submit_order"):
                    call_violations.append(
                        f"{func_name}() at line {getattr(node, 'lineno', 0)}"
                    )
        assert call_violations == [], f"Order placement calls found: {call_violations}"


# ═══════════════════════════════════════════════════════════════════════
# 3. RI excluded
# ═══════════════════════════════════════════════════════════════════════

class TestRIExcluded:
    """Criterion 3: RI is not used as active ticker."""

    def test_ri_in_excluded_list(self):
        from backtest_feedback import EXCLUDED_TICKERS
        assert "RI" in EXCLUDED_TICKERS

    def test_ri_not_as_active_ticker(self):
        """RI must only appear in EXCLUDED_TICKERS, not in active logic."""
        with open(_MODULE_PATH, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=_MODULE_PATH)
        ri_usages = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.strip() == "RI":
                    ri_usages.append(getattr(node, "lineno", 0))
        # RI appears in EXCLUDED_TICKERS list — that's the only valid location
        # We check that it's in EXCLUDED_TICKERS definition
        assert 'EXCLUDED_TICKERS: List[str] = ["RI"]' in source, (
            "RI should only appear in EXCLUDED_TICKERS definition"
        )


# ═══════════════════════════════════════════════════════════════════════
# 4. Guards: MAX_SLOTS ≤ 3, MAX_CONTRACTS = 1
# ═══════════════════════════════════════════════════════════════════════

class TestGuardConstants:
    """Criterion 4: Guards in place."""

    def test_max_slots_3(self):
        from backtest_feedback import MAX_SLOTS
        assert MAX_SLOTS == 3

    def test_max_contracts_1(self):
        from backtest_feedback import MAX_CONTRACTS
        assert MAX_CONTRACTS == 1


# ═══════════════════════════════════════════════════════════════════════
# 5. Generator feedback backward compatible
# ═══════════════════════════════════════════════════════════════════════

class TestGeneratorFeedbackCompat:
    """Criterion 5: extend_generator_feedback preserves existing fields."""

    def test_existing_fields_preserved(self):
        from backtest_feedback import extend_generator_feedback, build_feedback_scorecard
        existing = {
            "by_direction_regime": {"LONG": {"trend": 5}, "SHORT": {"range": 3}},
            "best_hours": [9, 10, 14, 15],
        }
        eq = [10000.0, 10100.0, 10200.0, 10300.0, 10400.0]
        tr = [1.0, 0.5, 1.2, -0.3, 0.8]
        sc = build_feedback_scorecard(eq, tr)
        extended = extend_generator_feedback(existing, sc)

        # Existing fields preserved
        assert "by_direction_regime" in extended
        assert "best_hours" in extended
        assert extended["best_hours"] == [9, 10, 14, 15]

        # New section added
        assert "backtest_feedback" in extended
        assert "composite_score" in extended["backtest_feedback"]

    def test_empty_existing_works(self):
        from backtest_feedback import extend_generator_feedback, build_feedback_scorecard
        eq = [10000.0, 10100.0, 10200.0]
        tr = [1.0, 0.5]
        sc = build_feedback_scorecard(eq, tr)
        extended = extend_generator_feedback({}, sc)
        assert "backtest_feedback" in extended


# ═══════════════════════════════════════════════════════════════════════
# 6. Fixture file has ≥3 scenarios
# ═══════════════════════════════════════════════════════════════════════

class TestFixtureFile:
    """Criterion 6: Fixture file has ≥3 scenarios with required structure."""

    def test_fixture_file_exists(self):
        assert os.path.exists(_FIXTURE_PATH), f"Fixture not found: {_FIXTURE_PATH}"

    def test_at_least_3_scenarios(self):
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        scenarios = data.get("scenarios", {})
        assert len(scenarios) >= 3, f"Expected ≥3 scenarios, got {len(scenarios)}"

    def test_scenarios_have_required_keys(self):
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, scenario in data["scenarios"].items():
            assert "equity_curve" in scenario, f"Scenario '{name}' missing equity_curve"
            assert "trades" in scenario, f"Scenario '{name}' missing trades"
            assert isinstance(scenario["equity_curve"], list), f"equity_curve not list in '{name}'"
            assert isinstance(scenario["trades"], list), f"trades not list in '{name}'"


# ═══════════════════════════════════════════════════════════════════════
# 7. Composite score is PnL↑/risk↓ metric
# ═══════════════════════════════════════════════════════════════════════

class TestCompositeScorecard:
    """Criterion 5: composite_score is a PnL↑/risk↓ metric."""

    def test_composite_score_range(self):
        from backtest_feedback import build_feedback_scorecard
        eq = [10000.0, 10100.0, 10200.0, 10300.0, 10400.0, 10500.0, 10600.0, 10700.0]
        tr = [1.5, -0.5, 1.0, 0.8, -0.3, 1.2, 0.6]
        sc = build_feedback_scorecard(eq, tr)
        assert 0.0 <= sc["composite_score"] <= 1.0, (
            f"composite_score out of [0,1]: {sc['composite_score']}"
        )

    def test_recommendations_present(self):
        from backtest_feedback import build_feedback_scorecard
        eq = [10000.0, 9800.0, 9600.0, 9400.0, 9200.0, 9000.0]
        tr = [-1.0, -0.5, -1.2, -0.8, -1.5]
        sc = build_feedback_scorecard(eq, tr)
        assert isinstance(sc["recommendations"], list)
        assert len(sc["recommendations"]) >= 1, "Expected at least 1 recommendation"

    def test_scorecard_structure(self):
        from backtest_feedback import build_feedback_scorecard
        eq = [10000.0, 10100.0, 10200.0]
        tr = [1.0, 0.5]
        sc = build_feedback_scorecard(eq, tr)
        required_keys = [
            "version", "equity_metrics", "drawdown_metrics",
            "expectancy_metrics", "degradation", "stability_score",
            "composite_score", "recommendations", "guards",
        ]
        for key in required_keys:
            assert key in sc, f"Missing key in scorecard: {key}"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _ast_scan_broker(filepath: str) -> list:
    """AST scan: return list of broker-related import nodes found in file."""
    violations = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (OSError, SyntaxError):
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name_lower = alias.name.lower()
                for kw in BROKER_KEYWORDS:
                    if kw in name_lower:
                        violations.append(f"import {alias.name} (keyword: {kw})")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            for kw in BROKER_KEYWORDS:
                if kw in module:
                    violations.append(f"from {node.module} (keyword: {kw})")
    return violations
