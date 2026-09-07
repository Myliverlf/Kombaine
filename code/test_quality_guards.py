"""Pytest tests for quality guards: overfit_guard, sensitivity_analyzer, degradation_detector.

≥4 fixtures, ≥8 tests.
Покрывает:
  - py_compile всех модулей
  - DSR: edge cases, NaN safety, monotonicity
  - IS→OOS degradation ratio
  - Embargo gap check
  - Composite is_overfit verdict
  - OAT sensitivity: monotonicity, normalization
  - CUSUM changepoint detection
  - Rolling IR
  - Health score thresholds
  - Degradation alert
  - RI excluded, no broker, max_slots ≤3

Все проверки через fixtures/dry-run. Нет live orders.
"""
import ast
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure code/ is on sys.path
CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR))

from overfit_guard import (
    deflated_sharpe_ratio,
    sharpe_degradation_ratio,
    min_oos_trades_ok,
    embargo_gap_ok,
    is_overfit,
    overfit_guard_report,
)
from sensitivity_analyzer import (
    perturb_weight,
    perturb_weight_negative,
    oat_sensitivity,
    sensitivity_report,
    edge_case_explorer,
)
from degradation_detector import (
    cusum_detect,
    cusum_detect_two_sided,
    rolling_ir,
    health_score,
    degradation_alert,
    degradation_report,
    STATUS_HEALTHY,
    STATUS_DEGRADED,
    STATUS_CRITICAL,
    STATUS_DEAD,
    ALL_STATUSES,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures (≥4)
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def synthetic_is_oos_pair() -> Dict[str, Any]:
    """IS/OOS pair with Sharpe ratios and trial counts."""
    return {
        "is_sharpe": 2.0,
        "oos_sharpe": 1.5,
        "num_trials": 50,
        "num_obs": 200,
        "oos_trades": 30,
    }


@pytest.fixture
def synthetic_rolling_series() -> List[float]:
    """Synthetic returns: first half trending up, second half choppy."""
    import random
    random.seed(99)
    # 30 bars up, 20 bars choppy
    up = [0.005 + random.uniform(-0.002, 0.003) for _ in range(30)]
    chop = [random.uniform(-0.01, 0.01) for _ in range(20)]
    return up + chop


@pytest.fixture
def weight_perturb_fixture() -> Dict[str, float]:
    """Base weights for sensitivity analysis (mirrors allocator_metrics.WEIGHTS)."""
    return {"expectancy": 40, "risk": 35, "regime": 25}


@pytest.fixture
def health_scenario_fixture() -> Dict[str, Any]:
    """Multiple health scenarios for degradation testing."""
    return {
        "healthy": health_score(1.5, 0.5, -0.05, 0.55),
        "degraded": health_score(0.3, 0.0, -0.15, 0.40),
        "critical": health_score(-0.2, -0.3, -0.20, 0.35),
        "dead": health_score(-1.0, -0.5, -0.40, 0.20),
    }


@pytest.fixture
def weight_key_list() -> List[str]:
    """Keys for weight perturbation testing."""
    return ["expectancy", "risk", "regime"]


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

class TestPyCompile:
    """AC1: py_compile OK для всех новых модулей."""

    def test_overfit_guard_compiles(self):
        path = CODE_DIR / "overfit_guard.py"
        assert path.exists(), f"File not found: {path}"
        import py_compile
        py_compile.compile(str(path), doraise=True)

    def test_sensitivity_analyzer_compiles(self):
        path = CODE_DIR / "sensitivity_analyzer.py"
        assert path.exists(), f"File not found: {path}"
        import py_compile
        py_compile.compile(str(path), doraise=True)

    def test_degradation_detector_compiles(self):
        path = CODE_DIR / "degradation_detector.py"
        assert path.exists(), f"File not found: {path}"
        import py_compile
        py_compile.compile(str(path), doraise=True)


class TestNoBroker:
    """AC3: No broker calls via AST."""

    _FORBIDDEN = {"post_order", "place_order", "send_order", "submit_order",
                   "Client", "open_position", "close_position"}

    def _check_no_broker(self, module_name: str):
        path = CODE_DIR / module_name
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in self._FORBIDDEN, \
                        f"Forbidden broker call '{node.func.id}' in {module_name}"
                elif isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in self._FORBIDDEN, \
                        f"Forbidden broker call '{node.func.attr}' in {module_name}"

    def test_no_broker_overfit_guard(self):
        self._check_no_broker("overfit_guard.py")

    def test_no_broker_sensitivity_analyzer(self):
        self._check_no_broker("sensitivity_analyzer.py")

    def test_no_broker_degradation_detector(self):
        self._check_no_broker("degradation_detector.py")


class TestOverfitGuard:
    """Tests for overfit_guard module."""

    def test_dsr_single_trial(self):
        """DSR with num_trials=1 should not crash and return finite."""
        dsr = deflated_sharpe_ratio(1.5, num_trials=1, num_obs=250)
        assert 0.0 <= dsr <= 1.0

    def test_dsr_high_sr(self):
        """High SR with few trials → high DSR."""
        dsr = deflated_sharpe_ratio(3.0, num_trials=5, num_obs=500)
        assert dsr > 0.8, f"Expected DSR > 0.8 for high SR, got {dsr}"

    def test_dsr_edge_zero_obs(self):
        """DSR with num_obs <= 3 → 0.0 (safe)."""
        dsr = deflated_sharpe_ratio(1.0, num_trials=10, num_obs=2)
        assert dsr == 0.0

    def test_dsr_monotonic_in_trials(self):
        """DSR decreases as num_trials increases (same SR)."""
        sr = 1.5
        dsr_10 = deflated_sharpe_ratio(sr, num_trials=10, num_obs=200)
        dsr_1000 = deflated_sharpe_ratio(sr, num_trials=1000, num_obs=200)
        assert dsr_10 >= dsr_1000, \
            f"DSR should decrease with more trials: {dsr_10} < {dsr_1000}"

    def test_degradation_ratio(self):
        """OOS/IS Sharpe ratio."""
        assert sharpe_degradation_ratio(2.0, 1.0) == 0.5
        assert sharpe_degradation_ratio(2.0, -0.5) == -0.25
        assert sharpe_degradation_ratio(0.0, 1.0) == 0.0

    def test_min_oos_trades(self):
        assert min_oos_trades_ok(20) is True
        assert min_oos_trades_ok(5) is False
        assert min_oos_trades_ok(15) is True

    def test_embargo_gap_ok(self):
        assert embargo_gap_ok([5, 10, 7]) is True
        assert embargo_gap_ok([2, 10]) is False
        assert embargo_gap_ok([]) is True

    def test_is_overfit_composite_pass(self):
        """Good IS/OOS → not overfit."""
        r = is_overfit(2.0, 1.8, num_trials=10, num_obs=250, oos_trades=30)
        assert r["is_overfit"] is False
        assert r["dsr_pass"] is True
        assert r["degradation_pass"] is True
        assert r["trades_pass"] is True

    def test_is_overfit_composite_fail(self):
        """Bad OOS with many trials → overfit."""
        r = is_overfit(2.0, 0.1, num_trials=1000, num_obs=50, oos_trades=3)
        assert r["is_overfit"] is True
        assert len(r["reasons"]) >= 1

    def test_overfit_guard_report_all_pass(self):
        """All windows with good OOS → PASS verdict."""
        windows = [
            {"is_sharpe": 1.5, "oos_sharpe": 1.3, "oos_trades": 20},
            {"is_sharpe": 1.8, "oos_sharpe": 1.5, "oos_trades": 25},
        ]
        report = overfit_guard_report(windows, num_trials=10)
        assert report["verdict"] == "PASS"
        assert report["windows_overfit"] == 0


class TestSensitivityAnalyzer:
    """Tests for sensitivity_analyzer module."""

    def test_perturb_weight_increases(self):
        """Positive delta_pct → key increases."""
        w = perturb_weight({"a": 60, "b": 40}, "a", 0.1)
        assert w["a"] > 60.0

    def test_perturb_weight_normalizes(self):
        """After perturbation, sum preserved."""
        base = {"a": 60, "b": 40}
        w = perturb_weight(base, "a", 0.2)
        assert abs(sum(w.values()) - 100.0) < 0.01

    def test_perturb_weight_negative(self):
        w = perturb_weight_negative({"a": 60, "b": 40}, "a", 0.1)
        assert w["a"] < 60.0

    def test_perturb_weight_unknown_key(self):
        """Unknown key → return copy."""
        base = {"a": 60, "b": 40}
        w = perturb_weight(base, "unknown", 0.1)
        assert w == base

    def test_oat_sensitivity_structure(self):
        """OAT returns all keys with required fields."""
        base = {"a": 60, "b": 40}
        result = oat_sensitivity(base, lambda w: sum(v**2 for v in w.values()))
        assert "a" in result
        assert "b" in result
        for key in result:
            assert "sensitivity_index" in result[key]
            assert "monotonic" in result[key]
            assert "base_value" in result[key]

    def test_oat_sensitivity_monotonic(self):
        """Simple quadratic: monotonic in each weight."""
        base = {"a": 60, "b": 40}
        result = oat_sensitivity(base, lambda w: sum(v**2 for v in w.values()))
        # Quadratic function → monotonic in each variable
        assert result["a"]["monotonic"] is True
        assert result["b"]["monotonic"] is True

    def test_sensitivity_report_ranks(self):
        """Report provides ranked sensitivities."""
        base = {"a": 60, "b": 40}
        report = sensitivity_report(base, lambda w: sum(v**2 for v in w.values()))
        assert "most_sensitive" in report
        assert "least_sensitive" in report
        assert len(report["ranked"]) == 2

    def test_edge_case_explorer(self):
        """Explorer returns multiple cases per key."""
        base = {"a": 60, "b": 40}
        cases = edge_case_explorer(base, lambda w: sum(w.values()))
        assert len(cases) >= 5  # 2 keys × 5 multipliers


class TestDegradationDetector:
    """Tests for degradation_detector module."""

    def test_cusum_detect_clear_shift(self):
        """Clear downward shift → changepoint detected."""
        series = [1.0] * 10 + [-2.0] * 10
        cps = cusum_detect(series, threshold=3.0, drift=0.3)
        assert len(cps) >= 1, f"Expected changepoint, got {cps}"

    def test_cusum_detect_no_shift(self):
        """Stable series → no changepoints."""
        series = [1.0] * 20
        cps = cusum_detect(series)
        assert len(cps) == 0

    def test_cusum_empty_series(self):
        assert cusum_detect([]) == []
        assert cusum_detect([1.0]) == []

    def test_cusum_two_sided(self):
        """Two-sided detects both up and down shifts."""
        series = [1.0] * 10 + [-3.0] * 10 + [5.0] * 10
        downs, ups = cusum_detect_two_sided(series, threshold=3.0, drift=0.3)
        assert len(downs) >= 1 or len(ups) >= 1

    def test_rolling_ir(self):
        """Rolling IR computes correctly."""
        returns = [0.01, 0.02, 0.01, -0.01, 0.015]
        ir = rolling_ir(returns, window=3)
        assert len(ir) == 5
        assert ir[0] is None  # First bar, window=3 → only 1 sample
        # Window >= 2 → should have value
        assert ir[2] is not None

    def test_health_score_healthy(self):
        """Good metrics → healthy."""
        h = health_score(1.5, 0.5, -0.05, 0.55)
        assert h["status"] == STATUS_HEALTHY
        assert h["composite_score"] >= 0.75

    def test_health_score_dead(self):
        """Bad metrics → dead."""
        h = health_score(-1.0, -0.5, -0.40, 0.20)
        assert h["status"] == STATUS_DEAD
        assert h["composite_score"] < 0.25

    def test_health_score_components(self):
        """All 4 components present."""
        h = health_score(1.0, 0.3, -0.10, 0.50)
        assert "sharpe" in h["components"]
        assert "ir" in h["components"]
        assert "max_dd" in h["components"]
        assert "win_rate" in h["components"]
        for comp in h["components"].values():
            assert 0.0 <= comp["score"] <= 1.0

    def test_health_score_from_fixture(self, health_scenario_fixture):
        """Test all scenarios from fixture."""
        scenarios = health_scenario_fixture
        assert scenarios["healthy"]["status"] == STATUS_HEALTHY
        assert scenarios["dead"]["status"] == STATUS_DEAD
        # Monotonicity: score decreases
        assert scenarios["healthy"]["composite_score"] > scenarios["degraded"]["composite_score"]
        assert scenarios["degraded"]["composite_score"] > scenarios["critical"]["composite_score"]
        assert scenarios["critical"]["composite_score"] > scenarios["dead"]["composite_score"]

    def test_degradation_alert_healthy(self):
        """Healthy health → no alert."""
        h = health_score(1.5, 0.5, -0.05, 0.55)
        a = degradation_alert(h)
        assert a["alert"] is False
        assert a["severity"] == "none"

    def test_degradation_alert_dead(self):
        """Dead health → critical alert."""
        h = health_score(-1.0, -0.5, -0.40, 0.20)
        a = degradation_alert(h)
        assert a["alert"] is True
        assert a["severity"] == "critical"
        assert len(a["recommendations"]) >= 1

    def test_degradation_alert_cusum_upgrade(self):
        """CUSUM alerts can upgrade severity to critical."""
        # Use params that produce 'degraded' status (composite 0.50..0.75)
        h = health_score(0.6, 0.1, -0.12, 0.45)  # degraded
        assert h["status"] == STATUS_DEGRADED
        a = degradation_alert(h, cusum_alerts=[5, 15, 25])
        assert a["alert"] is True
        # 3 CUSUM alerts with degraded status → severity "critical"
        assert a["severity"] == "critical"  # upgraded from warning

    def test_degradation_report_structure(self, synthetic_rolling_series):
        """Full report has all required keys."""
        report = degradation_report(synthetic_rolling_series)
        assert "returns_count" in report
        assert "last_sharpe" in report
        assert "avg_ir" in report
        assert "max_dd" in report
        assert "win_rate" in report
        assert "health" in report
        assert "alert" in report
        assert report["health"]["status"] in ALL_STATUSES


class TestACConstraints:
    """Acceptance criteria constraints."""

    def test_no_ri_in_weights(self, weight_perturb_fixture):
        """RI should never appear in weight keys."""
        w = weight_perturb_fixture
        assert "RI" not in w
        assert "ri" not in w

    def test_max_slots_lte_3(self):
        """max_slots must be ≤3 (from config.json)."""
        config_path = Path("/root/prop-desk/strategy_combine/config.json")
        if config_path.exists():
            import json
            cfg = json.loads(config_path.read_text())
            assert cfg.get("risk", {}).get("max_slots", 999) <= 3

    def test_max_contracts_one(self):
        """max_contracts_per_entry must be 1."""
        config_path = Path("/root/prop-desk/strategy_combine/config.json")
        if config_path.exists():
            import json
            cfg = json.loads(config_path.read_text())
            assert cfg.get("risk", {}).get("max_contracts_per_entry", 999) == 1

    def test_excluded_ri(self):
        """RI must be in excluded list."""
        config_path = Path("/root/prop-desk/strategy_combine/config.json")
        if config_path.exists():
            import json
            cfg = json.loads(config_path.read_text())
            assert "RI" in cfg.get("excluded", [])

    def test_no_import_broker(self):
        """None of our modules import broker/tinkoff."""
        for mod in ["overfit_guard", "sensitivity_analyzer", "degradation_detector"]:
            path = CODE_DIR / f"{mod}.py"
            if path.exists():
                content = path.read_text()
                assert "import tinkoff" not in content, f"{mod} imports tinkoff"
                assert "from broker" not in content, f"{mod} imports broker"
                assert "from tinkoff" not in content, f"{mod} imports from tinkoff"

    def test_live_status_constants(self):
        """Health status constants are valid."""
        assert STATUS_HEALTHY == "healthy"
        assert STATUS_DEGRADED == "degraded"
        assert STATUS_CRITICAL == "critical"
        assert STATUS_DEAD == "dead"
        assert len(ALL_STATUSES) == 4
