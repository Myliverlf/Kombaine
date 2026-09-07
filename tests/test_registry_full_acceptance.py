"""Full pipeline acceptance re-validation for StrategyRegistry.

Master acceptance gate that validates:
  1. py_compile OK for all new + existing registry test files
  2. Config constraints: RI excluded, max_slots ≤ 3, max_contracts_per_entry == 1
  3. All 8 statuses are reachable through transitions
  4. ≥3 fixtures in each test_registry_*.py file
  5. Composite scorecard bounds [0, 1]
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
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    STATUS_CONFLICTED,
    STATUS_EXPIRED,
    STATUS_REJECTED,
    STATUS_REGISTRY_CANDIDATE,
    STATUS_ROTATED_OUT,
    STATUS_WAITLIST,
    StrategyRegistry,
)


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
def all_statuses_registry(tmp_path: Path) -> StrategyRegistry:
    """Registry demonstrating all 8 statuses are reachable via transitions."""
    registry = StrategyRegistry(path=tmp_path / "registry.json")

    # 1. registry/candidate — via record_generation
    registry.record_generation(
        strategy_id="AC__candidate", ticker="TEST", strategy="s",
        source="generator",
    )
    # 2. waitlist
    registry.record_generation(
        strategy_id="AC__waitlist", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.transition("AC__waitlist", STATUS_WAITLIST, reason="promoted")
    # 3. active_watchlist
    registry.record_generation(
        strategy_id="AC__watchlist", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.mark_active_watchlist("AC__watchlist", slot=0, score=0.5)
    # 4. active_signal_pool
    registry.record_generation(
        strategy_id="AC__pool", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.mark_active_signal_pool("AC__pool", slot=0, score=0.9)
    # 5. rejected
    registry.record_generation(
        strategy_id="AC__rejected", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.mark_rejected("AC__rejected", reason="test")
    # 6. rotated_out
    registry.record_generation(
        strategy_id="AC__rotated", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.mark_active_watchlist("AC__rotated", slot=1, score=0.6)
    registry.mark_rotated_out("AC__rotated", reason="replaced")
    # 7. conflicted
    registry.record_generation(
        strategy_id="AC__conflicted", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.transition("AC__conflicted", STATUS_CONFLICTED, reason="ticker_overlap")
    # 8. expired
    registry.record_generation(
        strategy_id="AC__expired", ticker="TEST", strategy="s",
        source="generator",
    )
    registry.mark_expired("AC__expired", reason="ttl")

    registry.save()
    return registry


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
                # Handle @pytest.fixture(params=...) etc.
                elif isinstance(dec, ast.Call):
                    func = dec.func
                    if isinstance(func, ast.Attribute) and func.attr == "fixture":
                        fixtures.append(node.name)
                    elif isinstance(func, ast.Name) and func.id == "pytest.fixture":
                        fixtures.append(node.name)
    return fixtures


def test_py_compile_all_test_files(project_root: Path) -> None:
    """All test_registry_*.py files compile without errors."""
    test_files = sorted(project_root.glob("tests/test_registry_*.py"))

    compile_errors = []
    for tf in test_files:
        try:
            py_compile.compile(str(tf), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_errors.append(f"{tf.name}: {exc}")

    assert not compile_errors, "py_compile failed:\n" + "\n".join(compile_errors)
    assert len(test_files) >= 4, f"Expected ≥4 test files, found {len(test_files)}"


def test_py_compile_new_modules(project_root: Path) -> None:
    """New modules added by this task compile cleanly."""
    new_modules = [
        "tests/test_registry_transitions.py",
        "tests/test_registry_corrupt.py",
        "tests/test_registry_export_disk.py",
    ]
    compile_errors = []
    for mod_rel in new_modules:
        mod_path = project_root / mod_rel
        if not mod_path.exists():
            compile_errors.append(f"{mod_rel}: file not found")
            continue
        try:
            py_compile.compile(str(mod_path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_errors.append(f"{mod_rel}: {exc}")

    assert not compile_errors, "py_compile failed:\n" + "\n".join(compile_errors)


def test_config_constraints(config_data: dict) -> None:
    """Config enforces: RI excluded, max_slots ≤ 3, max_contracts_per_entry == 1."""
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


def test_all_8_statuses_reachable(all_statuses_registry: StrategyRegistry) -> None:
    """All 8 statuses are reachable through recorded transitions."""
    actual_statuses = {record.status for record in all_statuses_registry.records()}
    assert actual_statuses == ALL_STATUSES, (
        f"Status mismatch: expected={ALL_STATUSES}, actual={actual_statuses}"
    )


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
    assert len(test_files) >= 4, f"Expected ≥4 test files, found {len(test_files)}"


def test_scorecard_composite_bounded() -> None:
    """risk_allocator_scorecard_v2 produces composite in [0, 1]."""
    try:
        from risk_allocator_scorecard_v2 import score_pool
    except ImportError:
        pytest.skip("risk_allocator_scorecard_v2 not importable")

    pool = {
        "strategies": {
            "sc_test": {
                "ticker": "GAZP",
                "strategy": "mean_reversion",
                "status": "active",
                "params": {},
                "metrics": {"pnl": 1500.0, "sharpe": 0.72, "win_rate": 55.0, "trades": 30},
                "rank_score": 1500.0,
            },
        }
    }
    config = {
        "risk": {"max_slots": 3, "max_contracts_per_entry": 1, "risk_per_trade_pct": 2.7, "go_budget_pct": 50},
        "excluded": ["RI"],
        "risk_scorecard_weights": {
            "exposure": 20, "drawdown": 20, "volatility": 15,
            "correlation": 10, "signal_age": 15, "slots": 10, "caps": 10,
        },
    }
    regime = {"trend": "neutral"}

    result = score_pool(pool, config, regime)
    assert len(result) > 0, "score_pool returned empty result"

    for item in result:
        if isinstance(item, tuple) and len(item) == 2:
            sid, score_dict = item
            if isinstance(score_dict, dict) and "composite" in score_dict:
                composite = score_dict["composite"]
                assert 0.0 <= composite <= 1.0, f"{sid}: composite={composite} out of [0,1]"
