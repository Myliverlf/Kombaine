"""Tests for integration_check — unified portfolio health check pipeline.

All tests run on tmp_path — no live broker, no real state/ writes.
≥5 fixtures as required.

Covers:
  - Overloaded portfolio detection
  - Enforce reduces to max_slots
  - Backup created before enforce
  - Restore returns original
  - Scorecard composite_score in [0, 1]
  - No live broker imports in integration_check.py
  - Active positions preserved during enforce
"""
import ast
import json
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from integration_check import run_integration_check  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def overloaded_8slots() -> dict:
    """8-slot portfolio with duplicates (GAZP ft_bband_rsi ×4) and 2 open positions."""
    return {
        "slots": {
            "s1": {"ticker": "LKOH", "strategy": "vwap_reversion", "contracts": 1,
                    "pnl_rub": 538.0,
                    "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 42000.0}},
            "s2": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1,
                    "pnl_rub": -30.0,
                    "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 83.0}},
            "s3": {"ticker": "LKOH", "strategy": "nateemma_basket_meanrev", "contracts": 1,
                    "pnl_rub": 0.0, "open_position": None},
            "s4": {"ticker": "LKOH", "strategy": "nfi_trend", "contracts": 1,
                    "pnl_rub": 0.0, "open_position": None},
            "s5": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1,
                    "pnl_rub": -82.0, "open_position": None},
            "s6": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1,
                    "pnl_rub": 0.0, "open_position": None},
            "s7": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1,
                    "pnl_rub": -58.0, "open_position": None},
            "s8": {"ticker": "GAZP", "strategy": "bollinger_reversion", "contracts": 1,
                    "pnl_rub": 102.0, "open_position": None},
        },
        "peak_equity": 42789.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def clean_3slots() -> dict:
    """Clean 3-slot portfolio with no violations."""
    return {
        "slots": {
            "s1": {"ticker": "LKOH", "strategy": "vwap_reversion", "contracts": 1,
                    "pnl_rub": 100.0,
                    "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 42000.0}},
            "s2": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1,
                    "pnl_rub": 50.0, "open_position": None},
            "s3": {"ticker": "SBER", "strategy": "mean_reversion", "contracts": 1,
                    "pnl_rub": -20.0, "open_position": None},
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def paper_config() -> dict:
    """Paper-mode config matching config.json structure."""
    return {
        "mode": "paper",
        "paper_first": True,
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
        },
        "excluded": ["RI"],
    }


@pytest.fixture
def active_positions_portfolio() -> dict:
    """Portfolio with 5 slots where 2 have open positions — enforce must keep them."""
    return {
        "slots": {
            "a1": {"ticker": "LKOH", "strategy": "vwap_reversion", "contracts": 1,
                    "pnl_rub": 200.0,
                    "open_position": {"direction": "LONG", "qty": 1, "entry_price": 18000.0}},
            "a2": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1,
                    "pnl_rub": -10.0,
                    "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 80.0}},
            "a3": {"ticker": "SBER", "strategy": "mean_reversion", "contracts": 1,
                    "pnl_rub": 50.0, "open_position": None},
            "a4": {"ticker": "LKOH", "strategy": "nfi_trend", "contracts": 1,
                    "pnl_rub": 0.0, "open_position": None},
            "a5": {"ticker": "GAZP", "strategy": "bollinger_reversion", "contracts": 1,
                    "pnl_rub": 10.0, "open_position": None},
        },
        "peak_equity": 20000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def enforce_config() -> dict:
    """Config that allows up to 2 slots (stricter than default)."""
    return {
        "mode": "paper",
        "paper_first": True,
        "risk": {
            "max_slots": 2,
            "max_contracts_per_entry": 1,
        },
        "excluded": ["RI"],
    }


# ── Tests ──────────────────────────────────────────────────────────

class TestOverloadedDetected:
    """Verify 8-slot overloaded portfolio is flagged."""

    def test_overloaded_detected(self, overloaded_8slots: dict, paper_config: dict, tmp_path: Path) -> None:
        """8 slots → verdict=VIOLATIONS or ENFORCED, composite_score < 1.0."""
        result = run_integration_check(overloaded_8slots, paper_config, tmp_dir=tmp_path / "chk")

        assert result["verdict"] in ("VIOLATIONS", "ENFORCED")
        assert result["composite_score"] < 1.0
        assert result["checks"]["validation_before"]["verdict"] == "VIOLATIONS"
        # Must detect slot count violation
        assert result["checks"]["validation_before"]["checks"]["slot_count"]["ok"] is False


class TestEnforceReduces:
    """Verify enforce reduces slots to max_slots."""

    def test_enforce_reduces_to_max_slots(
        self, overloaded_8slots: dict, paper_config: dict, tmp_path: Path
    ) -> None:
        """After enforce, after-count ≤ max_slots."""
        result = run_integration_check(overloaded_8slots, paper_config, tmp_dir=tmp_path / "chk")

        assert result["enforce_before"] == 8
        assert result["enforce_after"] <= paper_config["risk"]["max_slots"]
        assert result["enforce_after"] <= 3


class TestBackupCreated:
    """Verify backup is created before enforce."""

    def test_backup_created_before_enforce(
        self, overloaded_8slots: dict, paper_config: dict, tmp_path: Path
    ) -> None:
        """backup_ok is True after enforcement check."""
        result = run_integration_check(overloaded_8slots, paper_config, tmp_dir=tmp_path / "chk")

        assert result["backup_ok"] is True


class TestRestoreReturnsOriginal:
    """Verify restore returns tmp state to original."""

    def test_restore_returns_original(
        self, overloaded_8slots: dict, paper_config: dict, tmp_path: Path
    ) -> None:
        """restore_ok is True, meaning original was restored from backup."""
        result = run_integration_check(overloaded_8slots, paper_config, tmp_dir=tmp_path / "chk")

        assert result["restore_ok"] is True


class TestScorecard:
    """Verify composite score is in valid range."""

    def test_scorecard_composite_score(
        self, overloaded_8slots: dict, paper_config: dict, tmp_path: Path
    ) -> None:
        """composite_score ∈ [0.0, 1.0]."""
        result = run_integration_check(overloaded_8slots, paper_config, tmp_dir=tmp_path / "chk")

        assert 0.0 <= result["composite_score"] <= 1.0


class TestCleanPortfolioPasses:
    """Verify clean 3-slot portfolio passes all checks."""

    def test_clean_portfolio_all_pass(
        self, clean_3slots: dict, paper_config: dict, tmp_path: Path
    ) -> None:
        """Clean portfolio → PASS, composite_score = 1.0."""
        result = run_integration_check(clean_3slots, paper_config, tmp_dir=tmp_path / "chk")

        assert result["verdict"] == "PASS"
        assert result["composite_score"] == 1.0
        assert result["enforce_before"] == 3
        assert result["enforce_after"] == 3
        assert len(result["removed_slots"]) == 0


class TestNoLiveOrdersInCode:
    """AST scan on integration_check.py — no broker imports."""

    def test_no_live_orders_in_code(self) -> None:
        """AST scan integration_check.py for broker-related imports."""
        target = COMBINE_DIR / "code" / "integration_check.py"
        if not target.exists():
            pytest.skip("integration_check.py not found")

        source = target.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(target))

        broker_modules = {"tinkoff", "broker", "alpaca", "ib_insync", "InteractiveBrokers"}

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0].lower()
                    if root in broker_modules:
                        violations.append(f"import {alias.name} (line {node.lineno})")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0].lower()
                if root in broker_modules:
                    violations.append(f"from {node.module} import ... (line {node.lineno})")

        assert len(violations) == 0, f"Broker imports found: {violations}"


class TestActivePositionsPreserved:
    """Verify active positions are kept by enforce."""

    def test_active_positions_preserved(
        self, active_positions_portfolio: dict, enforce_config: dict, tmp_path: Path
    ) -> None:
        """Both open_position slots survive enforce when max_slots=2."""
        result = run_integration_check(
            active_positions_portfolio, enforce_config, tmp_dir=tmp_path / "chk"
        )

        # Enforce should keep exactly 2 slots
        assert result["enforce_after"] <= 2

        # Check that active positions are in removed_slots — they should NOT be
        removed = set(result["removed_slots"])
        assert "a1" not in removed, "active position a1 was removed"
        assert "a2" not in removed, "active position a2 was removed"
