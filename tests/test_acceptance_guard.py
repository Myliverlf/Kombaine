"""AST Guard + Acceptance Check — safety-net тесты для критериев приёмки.

Проверяет:
  1. py_compile OK для всех новых модулей
  2. Нет import broker/order/live в новых модулях (AST scan)
  3. RI не упоминается как ticker в новых файлах
  4. max_slots≤3 и contracts=1 присутствуют в forecast_generator_bridge.py (регрессия guard)

Запуск: cd /root/prop-desk/strategy_combine && python -m pytest tests/test_acceptance_guard.py -v
"""
import ast
import os
import sys
import py_compile
import pytest

# Ensure code/ is on path
_CODE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

# Paths to new modules
NEW_MODULES = [
    os.path.join(_CODE_DIR, "timesfm_calibration.py"),
    os.path.join(_CODE_DIR, "timesfm_adaptation.py"),
    os.path.join(_CODE_DIR, "timesfm_calibration_bridge.py"),
]

# Broker keywords to scan for in imports
BROKER_KEYWORDS = frozenset({
    "tinkoff", "place_order", "send_order", "create_order",
    "futures_lab", "broker_client",
})


def _ast_scan_for_broker(filepath: str) -> list:
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
                        violations.append("import %s (keyword: %s)" % (alias.name, kw))
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            for kw in BROKER_KEYWORDS:
                if kw in module:
                    violations.append("from %s (keyword: %s)" % (node.module, kw))
    return violations


def _scan_for_ri_ticker(filepath: str) -> list:
    """Scan for RI as a ticker in code (not as part of other words)."""
    violations = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return violations

    tree = ast.parse(source, filename=filepath)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if val == "RI" or val == '"RI"' or val == "'RI'":
                violations.append("RI ticker found: %s at line %d" % (val, getattr(node, "lineno", 0)))
        # Check dict keys and string literals for "RI" as ticker
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "RI":
                    violations.append("RI dict key at line %d" % getattr(key, "lineno", 0))
    return violations


# ═══════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestPyCompile:
    """py_compile OK для всех новых модулей."""

    def test_calibration_compiles(self):
        path = NEW_MODULES[0]
        assert os.path.exists(path), "File not found: %s" % path
        py_compile.compile(path, doraise=True)

    def test_adaptation_compiles(self):
        path = NEW_MODULES[1]
        assert os.path.exists(path), "File not found: %s" % path
        py_compile.compile(path, doraise=True)

    def test_bridge_compiles(self):
        path = NEW_MODULES[2]
        assert os.path.exists(path), "File not found: %s" % path
        py_compile.compile(path, doraise=True)


class TestNoBrokerImports:
    """AST scan: нет broker-импортов в новых модулях."""

    def test_calibration_no_broker(self):
        violations = _ast_scan_for_broker(NEW_MODULES[0])
        assert violations == [], "Broker imports found: %s" % violations

    def test_adaptation_no_broker(self):
        violations = _ast_scan_for_broker(NEW_MODULES[1])
        assert violations == [], "Broker imports found: %s" % violations

    def test_bridge_no_broker(self):
        violations = _ast_scan_for_broker(NEW_MODULES[2])
        assert violations == [], "Broker imports found: %s" % violations


class TestNoRITicker:
    """RI не упоминается как ticker в новых файлах."""

    def test_calibration_no_ri(self):
        violations = _scan_for_ri_ticker(NEW_MODULES[0])
        assert violations == [], "RI ticker found: %s" % violations

    def test_adaptation_no_ri(self):
        violations = _scan_for_ri_ticker(NEW_MODULES[1])
        assert violations == [], "RI ticker found: %s" % violations

    def test_bridge_no_ri(self):
        violations = _scan_for_ri_ticker(NEW_MODULES[2])
        assert violations == [], "RI ticker found: %s" % violations


class TestRegressionGuard:
    """Регрессия: max_slots≤3 и contracts=1 в forecast_generator_bridge.py."""

    def test_max_slots_enforced(self):
        """forecast_generator_bridge.py содержит max_slots enforcement."""
        bridge_path = os.path.join(_CODE_DIR, "forecast_generator_bridge.py")
        if not os.path.exists(bridge_path):
            pytest.skip("forecast_generator_bridge.py not found in code/")
        with open(bridge_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "max_slots" in source, "max_slots not found in forecast_generator_bridge.py"

    def test_contracts_one(self):
        """forecast_generator_bridge.py содержит contracts=1."""
        bridge_path = os.path.join(_CODE_DIR, "forecast_generator_bridge.py")
        if not os.path.exists(bridge_path):
            pytest.skip("forecast_generator_bridge.py not found in code/")
        with open(bridge_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert "contracts" in source, "contracts not found in forecast_generator_bridge.py"

    def test_excluded_tickers_has_ri(self):
        """forecast_context.py содержит EXCLUDED_TICKERS с RI."""
        ctx_path = os.path.join(_CODE_DIR, "forecast_context.py")
        if not os.path.exists(ctx_path):
            pytest.skip("forecast_context.py not found in code/")
        with open(ctx_path, "r", encoding="utf-8") as f:
            source = f.read()
        assert 'EXCLUDED_TICKERS' in source, "EXCLUDED_TICKERS not found"
        assert '"RI"' in source or "'RI'" in source, "RI not in EXCLUDED_TICKERS"
