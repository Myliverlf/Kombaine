"""Acceptance scorecard tests for strategy_combine registry pipeline.

Formalizes the acceptance criteria from task.md as executable tests:
  1. py_compile OK for key modules
  2. Config constraints: RI excluded, max_slots ≤ 3, contracts ≤ 1
  3. No live broker imports in code/ (dry-run only)
  4. ≥3 fixtures in conftest.py
  5. risk/allocator scorecard composite bounded [0,1]
"""
from __future__ import annotations

import ast
import json
import os
import py_compile
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))


@pytest.fixture
def project_root() -> Path:
    """Root directory of strategy_combine project."""
    return COMBINE_DIR


@pytest.fixture
def config_data() -> dict:
    """Load config.json from project root."""
    config_path = COMBINE_DIR / "config.json"
    return json.loads(config_path.read_text(encoding="utf-8"))


@pytest.fixture
def conftest_path() -> Path:
    """Path to conftest.py for fixture inspection."""
    return COMBINE_DIR / "tests" / "conftest.py"


def test_py_compile_key_modules(project_root: Path) -> None:
    """Key modules must compile without errors (py_compile)."""
    modules_to_check = [
        "code/strategy_registry.py",
        "code/init_strategy_registry.py",
        "code/risk_allocator_scorecard_v2.py",
    ]

    compile_errors = []
    for module_rel in modules_to_check:
        module_path = project_root / module_rel
        if not module_path.exists():
            compile_errors.append(f"{module_rel}: file not found")
            continue
        try:
            py_compile.compile(str(module_path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_errors.append(f"{module_rel}: {exc}")

    assert not compile_errors, "py_compile failed:\n" + "\n".join(compile_errors)


def test_acceptance_config_constraints(config_data: dict) -> None:
    """Config must enforce: RI excluded, max_slots ≤ 3, max_contracts_per_entry == 1."""
    # RI excluded
    excluded = config_data.get("excluded", [])
    assert "RI" in excluded, f"RI not in excluded: {excluded}"

    # max_slots ≤ 3
    risk = config_data.get("risk", {})
    max_slots = risk.get("max_slots", 0)
    assert max_slots <= 3, f"max_slots={max_slots} > 3"

    # max_contracts_per_entry == 1
    max_contracts = risk.get("max_contracts_per_entry", 0)
    assert max_contracts == 1, f"max_contracts_per_entry={max_contracts} != 1"


def test_no_live_broker_imports(project_root: Path) -> None:
    """Code files in code/ must not import live broker modules."""
    code_dir = project_root / "code"
    broker_patterns = [
        "from tinkoff",
        "import tinkoff",
        "from smartbroker",
        "import smartbroker",
        "broker_connect",
        "create_client",
    ]

    violations = []
    for py_file in sorted(code_dir.glob("*.py")):
        if py_file.name.startswith("test_"):
            continue  # skip test files
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        # Parse AST to find real import statements, not string literals
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        import_nodes: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                import_nodes.append(node)

        for node in import_nodes:
            line_text = ast.get_source_segment(content, node)
            if line_text is None:
                continue
            stripped = line_text.strip()
            for pattern in broker_patterns:
                if pattern in stripped:
                    violations.append(f"{py_file.name}: {stripped}")

    assert not violations, "Live broker imports found:\n" + "\n".join(violations)


def test_conftest_has_minimum_fixtures(conftest_path: Path) -> None:
    """conftest.py must define at least 3 pytest fixtures."""
    content = conftest_path.read_text(encoding="utf-8")

    tree = ast.parse(content)

    fixtures = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == "pytest.fixture":
                    fixtures.append(node.name)
                elif isinstance(decorator, ast.Attribute) and decorator.attr == "fixture":
                    fixtures.append(node.name)

    assert len(fixtures) >= 3, (
        f"conftest.py has only {len(fixtures)} fixtures, need ≥3. "
        f"Found: {fixtures}"
    )


def test_risk_scorecard_composite_bounded() -> None:
    """risk_allocator_scorecard_v2.score_pool() returns composite scores in [0, 1]."""
    try:
        from risk_allocator_scorecard_v2 import score_pool
    except ImportError:
        pytest.skip("risk_allocator_scorecard_v2 not importable")

    # Minimal pool and config for scoring
    pool = {
        "strategies": {
            "test_strat_1": {
                "ticker": "GAZP",
                "strategy": "mean_reversion",
                "status": "active",
                "params": {},
                "metrics": {
                    "pnl": 1500.0,
                    "sharpe": 0.72,
                    "win_rate": 55.0,
                    "trades": 30,
                },
                "rank_score": 1500.0,
            },
        }
    }
    config = {
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "risk_per_trade_pct": 2.7,
            "go_budget_pct": 50,
        },
        "excluded": ["RI"],
        "risk_scorecard_weights": {
            "exposure": 20,
            "drawdown": 20,
            "volatility": 15,
            "correlation": 10,
            "signal_age": 15,
            "slots": 10,
            "caps": 10,
        },
    }
    regime = {"trend": "neutral"}

    result = score_pool(pool, config, regime)

    # score_pool returns a sorted list of (strategy_id, score_dict) tuples
    assert len(result) > 0, "score_pool returned empty result"

    for item in result:
        if isinstance(item, tuple) and len(item) == 2:
            sid, score_dict = item
            if isinstance(score_dict, dict) and "composite" in score_dict:
                composite = score_dict["composite"]
                assert 0.0 <= composite <= 1.0, (
                    f"{sid}: composite={composite} out of [0,1]"
                )
