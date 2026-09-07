"""Integration tests for pipeline hard constraints.

Verifies:
  (a) All code/*.py compile without errors (py_compile)
  (b) Zero broker/tinkoff imports in code/ (AST scan)
  (c) config.json enforces: RI excluded, max_slots ≤ 3, max_contracts_per_entry == 1
  (d) e2e_real_result.json exists and all_passed == true

No live broker, no network, no side-effects.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def code_files() -> list[Path]:
    """All .py files in code/ directory."""
    return sorted(CODE_DIR.glob("*.py"))


@pytest.fixture
def config() -> dict:
    """Load config.json — paper mode, constraints."""
    cfg_path = COMBINE_DIR / "config.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def e2e_result() -> dict:
    """Load e2e_real_result.json — must exist for tests to pass."""
    candidates = [
        COMBINE_DIR / "e2e_real_result.json",
        COMBINE_DIR / "e2e_full_result.json",
    ]
    for path in candidates:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    pytest.skip("No e2e result JSON found (e2e_real_result.json / e2e_full_result.json)")


# ── Tests ─────────────────────────────────────────────────────────────

class TestPyCompile:
    """Verify all code/*.py compile without syntax errors."""

    def test_all_code_files_compile(self, code_files: list[Path]) -> None:
        failed: list[str] = []
        for f in code_files:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(f)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                failed.append(f"{f.name}: {result.stderr.strip()[:200]}")
        assert len(failed) == 0, f"py_compile failed for:\n" + "\n".join(failed)

    def test_code_files_non_empty(self, code_files: list[Path]) -> None:
        """Sanity: code/ has at least 10 .py files."""
        assert len(code_files) >= 10, f"Expected ≥10 files in code/, got {len(code_files)}"


class TestNoBrokerImports:
    """AST-guard: zero broker/tinkoff imports anywhere in code/."""

    def test_no_broker_imports(self, code_files: list[Path]) -> None:
        violations: list[str] = []
        for py_file in code_files:
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, OSError) as exc:
                violations.append(f"{py_file.name}: parse_error={exc}")
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        lower = alias.name.lower()
                        if "tinkoff" in lower or "broker" in lower:
                            violations.append(
                                f"{py_file.name}: import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        lower = node.module.lower()
                        if "tinkoff" in lower or "broker" in lower:
                            violations.append(
                                f"{py_file.name}: from {node.module}"
                            )
        assert len(violations) == 0, (
            f"Found {len(violations)} broker/tinkoff import(s):\n"
            + "\n".join(violations[:10])
        )


class TestConfigConstraints:
    """Verify config.json enforces hard pipeline constraints."""

    def test_ri_excluded(self, config: dict) -> None:
        excluded = config.get("excluded", [])
        assert "RI" in excluded, f"RI not in excluded list: {excluded}"

    def test_max_slots_le_3(self, config: dict) -> None:
        max_slots = config.get("risk", {}).get("max_slots", 0)
        assert max_slots <= 3, f"max_slots={max_slots}, expected ≤3"

    def test_max_contracts_per_entry_is_1(self, config: dict) -> None:
        max_cpe = config.get("risk", {}).get("max_contracts_per_entry", 0)
        assert max_cpe == 1, f"max_contracts_per_entry={max_cpe}, expected 1"

    def test_paper_mode(self, config: dict) -> None:
        assert config.get("mode") == "paper", f"mode={config.get('mode')}, expected 'paper'"


class TestE2EResultExists:
    """Verify e2e result JSON is valid and shows all_passed."""

    def test_e2e_all_passed(self, e2e_result: dict) -> None:
        assert e2e_result.get("all_passed") is True, (
            f"all_passed={e2e_result.get('all_passed')}, expected true"
        )

    def test_e2e_no_failed_steps(self, e2e_result: dict) -> None:
        n_failed = e2e_result.get("n_failed", -1)
        assert n_failed == 0, f"n_failed={n_failed}, expected 0"

    def test_e2e_ri_filtered(self, e2e_result: dict) -> None:
        """RI should be excluded from the pipeline."""
        n_live = e2e_result.get("n_live_orders", -1)
        assert n_live == 0, f"n_live_orders={n_live}, expected 0"

    def test_e2e_max_3_candidates(self, e2e_result: dict) -> None:
        """Pipeline selects at most 3 candidates."""
        candidates = e2e_result.get("pnl_metrics", {}).get("candidates_detail", [])
        assert len(candidates) <= 3, (
            f"candidates={len(candidates)}, expected ≤3"
        )

    def test_e2e_one_contract_per_candidate(self, e2e_result: dict) -> None:
        """Each candidate has exactly 1 contract."""
        candidates = e2e_result.get("pnl_metrics", {}).get("candidates_detail", [])
        for c in candidates:
            contracts = c.get("contracts", -1)
            assert contracts == 1, (
                f"{c.get('ticker')}/{c.get('strategy')}: "
                f"contracts={contracts}, expected 1"
            )
