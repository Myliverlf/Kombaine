"""Acceptance summary — aggregates all existing test_registry_* validation into one gate.

Validates:
  1. py_compile OK for all test_registry_*.py in tests/
  2. Config constraints: RI excluded, max_slots ≤ 3, max_contracts_per_entry == 1
  3. Scorecard composite ∈ [0, 1] for representative candidates
  4. ≥3 fixtures in each test_registry_*.py file
  5. Registry loads without error and has version field

No live broker, no network — pure validation logic.
"""
from __future__ import annotations

import ast
import json
import py_compile
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from strategy_registry import (  # noqa: E402
    ALL_STATUSES,
    StrategyRegistry,
)


# ─── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def project_root() -> Path:
    """Root directory of strategy_combine project."""
    return COMBINE_DIR


@pytest.fixture
def config_data() -> dict:
    """Load config.json for constraint checks."""
    config_path = COMBINE_DIR / "config.json"
    return json.loads(config_path.read_text(encoding="utf-8"))


@pytest.fixture
def empty_registry(tmp_path: Path) -> StrategyRegistry:
    """Empty registry for idempotency checks."""
    return StrategyRegistry(path=tmp_path / "summary_registry.json")


@pytest.fixture
def representative_candidate() -> dict:
    """A representative candidate dict for scorecard bound checks."""
    return {
        "ticker": "GAZP",
        "direction": "LONG",
        "win_rate": 0.55,
        "avg_win": 200.0,
        "avg_loss": 100.0,
        "drawdown_pct": 6.0,
        "volatility": 4.0,
        "risk_per_trade": 100.0,
    }


# ─── Helpers ────────────────────────────────────────────────────────

def _count_fixtures_in_file(path: Path) -> list[str]:
    """Count pytest.fixture-decorated functions in a Python file."""
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content)
    fixtures = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id == "pytest.fixture":
                    fixtures.append(node.name)
                elif isinstance(dec, ast.Attribute) and dec.attr == "fixture":
                    fixtures.append(node.name)
                elif isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Attribute) and func.attr == "fixture":
                        fixtures.append(node.name)
                    elif isinstance(func, ast.Name) and func.id == "pytest.fixture":
                        fixtures.append(node.name)
    return fixtures


# ─── Tests ──────────────────────────────────────────────────────────

def test_py_compile_all_test_files(project_root: Path) -> None:
    """All test_registry_*.py files compile without errors."""
    test_files = sorted(project_root.glob("tests/test_registry_*.py"))
    assert len(test_files) >= 4, f"Expected ≥4 test files, found {len(test_files)}"

    compile_errors = []
    for tf in test_files:
        try:
            py_compile.compile(str(tf), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_errors.append(f"{tf.name}: {exc}")

    assert not compile_errors, "py_compile failed:\n" + "\n".join(compile_errors)


def test_config_constraints(config_data: dict) -> None:
    """Config enforces: RI excluded, max_slots ≤ 3, max_contracts_per_entry == 1."""
    excluded = config_data.get("excluded", [])
    assert "RI" in excluded, f"RI not in excluded: {excluded}"

    risk = config_data.get("risk", {})
    max_slots = risk.get("max_slots", 0)
    assert max_slots <= 3, f"max_slots={max_slots} > 3"

    max_contracts = risk.get("max_contracts_per_entry", 0)
    assert max_contracts == 1, f"max_contracts_per_entry={max_contracts} != 1"


def test_each_file_has_at_least_3_fixtures(project_root: Path) -> None:
    """Each test_registry_*.py file defines ≥3 pytest fixtures."""
    test_files = sorted(project_root.glob("tests/test_registry_*.py"))
    issues = []

    for tf in test_files:
        fixtures = _count_fixtures_in_file(tf)
        count = len(fixtures)
        if count < 3:
            issues.append(f"{tf.name}: {count} fixtures ({fixtures}) — need ≥3")

    assert not issues, "Fixture count failures:\n" + "\n".join(issues)


def test_scorecard_composite_bounded(representative_candidate: dict) -> None:
    """risk_allocator_scorecard_v2 composite ∈ [0, 1] for a representative candidate."""
    try:
        from risk_allocator_scorecard_v2 import compute_composite
    except ImportError:
        pytest.skip("risk_allocator_scorecard_v2 not importable")

    result = compute_composite(
        representative_candidate,
        risk_per_trade=100.0,
    )

    composite = result.get("composite_score", -1.0)
    assert 0.0 <= composite <= 1.0, (
        f"composite_score={composite} out of [0, 1]; result={result}"
    )
    assert result.get("ticker") == "GAZP"


def test_registry_version_field_exists(empty_registry: StrategyRegistry) -> None:
    """Registry data has a version field set to 1."""
    data = empty_registry.data
    assert "version" in data, f"Missing version in registry data: {list(data.keys())}"
    assert data["version"] == 1, f"Expected version=1, got {data['version']}"


def test_all_statuses_are_strings() -> None:
    """ALL_STATUSES are non-empty strings."""
    for status in ALL_STATUSES:
        assert isinstance(status, str), f"Status {status!r} is not a string"
        assert len(status) > 0, "Status is an empty string"
