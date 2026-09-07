"""Acceptance guards — RI exclusion, slot/contract limits, no live orders.

≥3 fixture-based tests verifying hard acceptance criteria.
All tests are dry-run only; no broker calls, no network, no state writes.
"""
import ast
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))


# ── Test 1: RI excluded ──────────────────────────────────────────────

class TestRIExcluded:
    """Verify RI is excluded at every pipeline stage."""

    def test_ri_excluded_from_config(self, engine_config_fixture):
        """config.json excluded list must contain RI."""
        excluded = engine_config_fixture.get("excluded", [])
        assert "RI" in excluded, f"RI not in excluded list: {excluded}"

    def test_ri_excluded_from_allocator(self, engine_config_fixture):
        """select_live_slots must reject RI ticker."""
        from candidate_allocator import select_live_slots

        candidates = [
            {"ticker": "RI", "direction": "LONG",
             "win_rate": 0.95, "avg_win": 1000, "avg_loss": 10, "contracts_requested": 1},
            {"ticker": "LKOH", "direction": "LONG",
             "win_rate": 0.5, "avg_win": 100, "avg_loss": 50, "contracts_requested": 1},
        ]
        selected = select_live_slots(candidates, engine_config_fixture)
        tickers = [s["ticker"] for s in selected]
        assert "RI" not in tickers, f"RI leaked into allocation: {tickers}"

    def test_ri_excluded_from_scorecard(self, portfolio_fixture, engine_config_fixture):
        """check_ri_excluded must pass for clean portfolio."""
        from risk_allocator_scorecard import check_ri_excluded

        excluded = engine_config_fixture.get("excluded", [])
        r = check_ri_excluded(portfolio_fixture, excluded=excluded)
        assert r["passed"] is True, f"RI exclusion check failed: {r}"

    def test_ri_in_portfolio_detected(self, engine_config_fixture):
        """check_ri_excluded must fail when RI is in portfolio slots."""
        from risk_allocator_scorecard import check_ri_excluded

        portfolio_with_ri = {
            "slots": {
                "RI_nfi": {"ticker": "RI", "contracts": 1},
            }
        }
        excluded = engine_config_fixture.get("excluded", [])
        r = check_ri_excluded(portfolio_with_ri, excluded=excluded)
        assert r["passed"] is False, f"RI should be detected: {r}"


# ── Test 2: Max slots ≤ 3 ───────────────────────────────────────────

class TestMaxSlots:
    """Verify max_live_slots ≤ 3 enforced everywhere."""

    def test_config_max_slots_3(self, engine_config_fixture):
        """Config risk.max_slots must be exactly 3."""
        assert engine_config_fixture["risk"]["max_slots"] == 3, (
            f"Expected max_slots=3, got {engine_config_fixture['risk']['max_slots']}"
        )

    def test_allocator_respects_max_slots(self, engine_config_fixture):
        """Allocator must never return more than 3 slots."""
        from candidate_allocator import select_live_slots

        candidates = [
            {"ticker": f"T{i}", "direction": "LONG",
             "win_rate": 0.5 + i * 0.01, "avg_win": 100, "avg_loss": 50}
            for i in range(10)
        ]
        selected = select_live_slots(candidates, engine_config_fixture)
        assert len(selected) <= 3, f"Allocator returned {len(selected)} > 3"

    def test_scorecard_detects_overloaded(self, engine_config_fixture):
        """check_max_slots must fail when portfolio has >3 slots."""
        from risk_allocator_scorecard import check_max_slots

        overloaded = {
            "slots": {f"s{i}": {"ticker": f"T{i}"} for i in range(5)}
        }
        r = check_max_slots(overloaded, max_slots=3)
        assert r["passed"] is False, f"Should detect overloaded portfolio: {r}"


# ── Test 3: One contract per entry ──────────────────────────────────

class TestOneContract:
    """Verify max_contracts_per_entry == 1 enforced everywhere."""

    def test_config_max_contracts_1(self, engine_config_fixture):
        """Config risk.max_contracts_per_entry must be exactly 1."""
        assert engine_config_fixture["risk"]["max_contracts_per_entry"] == 1, (
            f"Expected 1, got {engine_config_fixture['risk']['max_contracts_per_entry']}"
        )

    def test_allocator_clamps_contracts(self, engine_config_fixture):
        """Allocator must clamp contracts_requested down to 1."""
        from candidate_allocator import select_live_slots

        candidates = [
            {"ticker": "LKOH", "direction": "LONG",
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 100,
             "contracts_requested": 10},
        ]
        selected = select_live_slots(candidates, engine_config_fixture)
        assert len(selected) == 1
        assert selected[0]["contracts"] == 1, (
            f"Expected contracts=1, got {selected[0]['contracts']}"
        )

    def test_scorecard_validates_contracts(self, engine_config_fixture):
        """check_contracts_per_entry must pass on valid portfolio."""
        from risk_allocator_scorecard import check_contracts_per_entry

        portfolio = {
            "slots": {
                "s1": {"ticker": "LKOH", "contracts": 1},
                "s2": {"ticker": "GAZP", "contracts": 1},
            }
        }
        r = check_contracts_per_entry(portfolio, max_contracts=1)
        assert r["passed"] is True, f"Should pass: {r}"


# ── Test 4: No live broker imports in pipeline modules ──────────────

class TestNoLiveBrokerImports:
    """AST-guard: no pipeline module imports broker client directly."""

    # Tinkoff invest imports that indicate broker access
    DANGEROUS_IMPORTS = {
        "investapi", "tinkoff.invest", "tinkoff",
        "broker_client", "BrokerClient",
    }

    def test_no_broker_imports_in_code_dir(self):
        """All .py files in code/ must not import broker libraries."""
        violations = []
        py_files = sorted(CODE_DIR.glob("*.py"))
        for py_file in py_files:
            if py_file.name.startswith("test_") or py_file.name == "validate_engine.py":
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.lower().replace(".", "") in {
                            d.lower().replace(".", "") for d in self.DANGEROUS_IMPORTS
                        }:
                            violations.append(f"{py_file.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.lower().replace(".", "") in {
                        d.lower().replace(".", "") for d in self.DANGEROUS_IMPORTS
                    }:
                        violations.append(f"{py_file.name}: from {node.module}")
        assert not violations, f"Broker imports found: {violations}"

    def test_config_mode_paper_in_fixture(self, engine_config_fixture):
        """engine_config_fixture must never be in live mode."""
        assert engine_config_fixture["mode"] != "live", (
            "Config mode='live' is dangerous for dry-run tests"
        )
