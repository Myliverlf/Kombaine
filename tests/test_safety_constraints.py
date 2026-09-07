"""Test Safety Constraints — pytest для проверки safety-ограничений pipeline.

Проверяет:
  (a) RI excluded — config.json excluded=["RI"]
  (b) no broker imports — AST-scan code/ на import tinkoff/invest
  (c) max_slots <= 3 и max_contracts_per_entry == 1 из config.json
  (d) mode == "paper"

Все проверки read-only, не модифируют state/.
Live orders запрещены.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

# ─── Fixtures ──────────────────────────────────────────────────────────

CONFIG_PATH = COMBINE_DIR / "config.json"
CODE_DIR_PATH = COMBINE_DIR / "code"

# Forbidden imports in code/ (broker/tinkoff connections)
FORBIDDEN_IMPORTS = {"tinkoff", "invest", "tinkoff_invest", "tinkoff.trading"}


@pytest.fixture
def config_data() -> dict:
    """Load config.json from strategy_combine project root."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def code_py_files() -> list[Path]:
    """List all .py files in code/ directory (excluding __pycache__)."""
    return [
        p
        for p in CODE_DIR_PATH.rglob("*.py")
        if "__pycache__" not in str(p) and ".pyc" not in str(p)
    ]


@pytest.fixture
def code_ast_modules(code_py_files) -> list[tuple[Path, ast.Module]]:
    """Parse all code/*.py files into AST modules."""
    modules = []
    for filepath in code_py_files:
        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(filepath))
            modules.append((filepath, tree))
        except SyntaxError:
            # py_compile will catch syntax errors separately
            continue
    return modules


# ─── Tests: RI Exclusion ───────────────────────────────────────────────

class TestRIExclusion:
    """Verify RI is excluded from the pipeline."""

    def test_ri_in_excluded_list(self, config_data):
        """config.json must have 'RI' in excluded list."""
        excluded = config_data.get("excluded", [])
        assert "RI" in excluded, f"RI not in excluded: {excluded}"

    def test_ri_excluded_in_default_loader(self):
        """data_loader.DEFAULT_EXCLUDED must contain 'RI'."""
        from data_loader import DEFAULT_EXCLUDED
        assert "RI" in DEFAULT_EXCLUDED

    def test_ri_excluded_from_scorecard(self):
        """compute_scorecard with RI in input must not score RI."""
        from risk_scorecard import compute_scorecard
        sc = compute_scorecard(["RI", "SBER"], excluded=["RI"])
        assert "RI" not in sc["tickers"], "RI should not appear in scored tickers"
        assert len(sc.get("excluded_found", [])) >= 1, "RI should be in excluded_found"


# ─── Tests: No Broker Imports ──────────────────────────────────────────

class TestNoBrokerImports:
    """AST-scan code/ for forbidden broker/tinkoff imports."""

    def test_no_tinkoff_imports(self, code_ast_modules):
        """No file in code/ should import tinkoff or invest modules."""
        violations = []
        for filepath, tree in code_ast_modules:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name.lower().split(".")[0]
                        if name in FORBIDDEN_IMPORTS:
                            violations.append(
                                f"{filepath.name}:{node.lineno}: import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top = node.module.lower().split(".")[0]
                        if top in FORBIDDEN_IMPORTS:
                            violations.append(
                                f"{filepath.name}:{node.lineno}: from {node.module}"
                            )
        assert violations == [], f"Broker imports found: {violations}"

    def test_no_live_order_functions(self, code_ast_modules):
        """No function should contain 'execute_order' or 'place_order' as name."""
        danger_names = {"execute_order", "place_order", "send_order", "submit_order"}
        violations = []
        for filepath, tree in code_ast_modules:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.lower() in danger_names:
                        violations.append(
                            f"{filepath.name}:{node.lineno}: def {node.name}"
                        )
        assert violations == [], f"Live order functions found: {violations}"


# ─── Tests: Config Safety Limits ───────────────────────────────────────

class TestConfigSafetyLimits:
    """Verify max_slots, max_contracts, mode from config.json."""

    def test_max_slots_at_most_3(self, config_data):
        """max_slots must be <= 3."""
        max_slots = config_data.get("risk", {}).get("max_slots", 0)
        assert max_slots <= 3, f"max_slots={max_slots} > 3"

    def test_max_contracts_per_entry_is_1(self, config_data):
        """max_contracts_per_entry must be exactly 1."""
        max_cpe = config_data.get("risk", {}).get("max_contracts_per_entry", 0)
        assert max_cpe == 1, f"max_contracts_per_entry={max_cpe} != 1"

    def test_mode_is_paper(self, config_data):
        """config mode must be 'paper' (no live trading)."""
        mode = config_data.get("mode", "unknown")
        assert mode == "paper", f"mode={mode} != 'paper'"

    def test_paper_first_is_true(self, config_data):
        """paper_first must be True."""
        pf = config_data.get("paper_first", False)
        assert pf is True, f"paper_first={pf} != True"

    def test_universe_no_dangerous_tickers(self, config_data):
        """Universe must only contain known safe tickers."""
        universe = set(config_data.get("universe", []))
        # FIX: universe expanded to all instruments with real data on disk
        # (NG excluded — no data). RI remains forbidden (see excluded list).
        expected = {"BR", "GAZP", "LKOH", "SBER", "Si", "CNY", "EURRUB", "USDRUB", "IMOEX"}
        assert universe == expected, f"Universe mismatch: {universe} != {expected}"
        assert "RI" not in universe
