"""Integration tests for backup + live_order_guard + end-to-end validator.

Tests backup/restore on tmp dirs (never touching real state/).
Tests live_order_guard on conftest fixtures.
Tests full overloaded portfolio validation flow.

Источники:
  - plan.md Фича 5
  - code/state_backup.py
  - code/live_order_guard.py
  - code/portfolio_validator.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))

from state_backup import snapshot_state, atomic_write_json, restore_from_backup, list_backups  # noqa: E402
from live_order_guard import assert_no_broker_imports, assert_paper_mode, generate_audit_report  # noqa: E402
from portfolio_validator import validate_portfolio  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Create a temporary state directory with sample JSON files."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Write sample portfolio
    portfolio = {
        "slots": {
            "slot_A": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 100.0,
                "open_position": None,
            },
        },
        "peak_equity": 20000.0,
        "halted": False,
        "halt_reason": None,
    }
    (state_dir / "portfolio.json").write_text(json.dumps(portfolio, indent=2))

    # Write sample config
    config = {"mode": "paper", "paper_first": True}
    (state_dir / "config.json").write_text(json.dumps(config, indent=2))

    # Write sample registry
    registry = {"strategies": {}}
    (state_dir / "strategy_registry.json").write_text(json.dumps(registry, indent=2))

    return state_dir


@pytest.fixture
def overloaded_portfolio_fixture() -> dict:
    """8-slot overloaded portfolio for integration test."""
    return {
        "slots": {
            "s1": {"ticker": "LKOH", "strategy": "vwap_reversion", "contracts": 1, "pnl_rub": 538.0, "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 42000.0}},
            "s2": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1, "pnl_rub": -30.0, "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 83.0}},
            "s3": {"ticker": "LKOH", "strategy": "nateemma_basket_meanrev", "contracts": 1, "pnl_rub": 0.0, "open_position": None},
            "s4": {"ticker": "LKOH", "strategy": "nfi_trend", "contracts": 1, "pnl_rub": 0.0, "open_position": None},
            "s5": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1, "pnl_rub": -82.0, "open_position": None},
            "s6": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1, "pnl_rub": 0.0, "open_position": None},
            "s7": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1, "pnl_rub": -58.0, "open_position": None},
            "s8": {"ticker": "GAZP", "strategy": "bollinger_reversion", "contracts": 1, "pnl_rub": 102.0, "open_position": None},
        },
        "peak_equity": 42789.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def paper_config_fixture() -> dict:
    """Safe paper-mode config."""
    return {
        "mode": "paper",
        "paper_first": True,
        "deposit_rub": 21281,
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
        },
        "excluded": ["RI"],
    }


# ── Tests: Backup & Restore ────────────────────────────────────────

class TestBackupRestore:
    """Tests for state_backup module on tmp dirs."""

    def test_snapshot_creates_bak_files(self, tmp_state_dir: Path) -> None:
        """snapshot_state creates .bak for every JSON in state/."""
        result = snapshot_state(tmp_state_dir)
        assert len(result["errors"]) == 0
        assert len(result["created"]) == 3  # portfolio, config, registry

        # Verify .bak files exist
        for bak in result["created"]:
            assert Path(bak).exists()
            assert bak.endswith(".json.bak")

    def test_atomic_write_and_restore(self, tmp_state_dir: Path) -> None:
        """atomic_write_json writes correctly; restore_from_backup reverts."""
        portfolio_path = tmp_state_dir / "portfolio.json"

        # Snapshot first
        snapshot_state(tmp_state_dir)

        # Read original
        original = json.loads(portfolio_path.read_text())
        original_pnl = original["slots"]["slot_A"]["pnl_rub"]

        # Mutate
        modified = json.loads(json.dumps(original))
        modified["slots"]["slot_A"]["pnl_rub"] = 99999.0
        atomic_write_json(portfolio_path, modified)

        # Verify mutation
        after_write = json.loads(portfolio_path.read_text())
        assert after_write["slots"]["slot_A"]["pnl_rub"] == 99999.0

        # Restore
        restore_result = restore_from_backup(portfolio_path)
        assert restore_result["restored"] is True
        assert restore_result["error"] is None

        # Verify restored
        restored = json.loads(portfolio_path.read_text())
        assert restored["slots"]["slot_A"]["pnl_rub"] == original_pnl

    def test_list_backups(self, tmp_state_dir: Path) -> None:
        """list_backups returns metadata for all .bak files."""
        snapshot_state(tmp_state_dir)
        backups = list_backups(tmp_state_dir)
        assert len(backups) == 3
        for b in backups:
            assert b["size_bytes"] > 0
            assert b["original"].endswith(".json")

    def test_restore_nonexistent_backup(self, tmp_path: Path) -> None:
        """restore_from_backup returns error when no .bak exists."""
        fake_path = tmp_path / "nonexistent.json"
        result = restore_from_backup(fake_path)
        assert result["restored"] is False
        assert "not found" in result["error"]


# ── Tests: Live Order Guard ────────────────────────────────────────

class TestLiveOrderGuard:
    """Tests for live_order_guard module."""

    def test_paper_mode_passes(self, paper_config_fixture: dict) -> None:
        """Paper mode config passes assert_paper_mode."""
        result = assert_paper_mode(paper_config_fixture)
        assert result["ok"] is True
        assert result["mode"] == "paper"
        assert result["paper_first"] is True

    def test_live_mode_fails(self) -> None:
        """Live mode config fails assert_paper_mode."""
        config = {"mode": "live", "paper_first": False}
        result = assert_paper_mode(config)
        assert result["ok"] is False
        assert len(result["issues"]) >= 1

    def test_no_broker_imports_in_code(self) -> None:
        """Scan code/ for actual broker import statements — not docstrings or regex patterns."""
        import re

        # Check only the new files we created
        new_files = [
            COMBINE_DIR / "code" / "portfolio_validator.py",
            COMBINE_DIR / "code" / "state_backup.py",
            COMBINE_DIR / "code" / "live_order_guard.py",
        ]
        # Patterns that match actual Python import statements
        broker_import_re = re.compile(
            r'^\s*(import\s+\S*tinkoff\S*|from\s+\S*tinkoff\S*\s+import|'
            r'import\s+\S*broker\S*|from\s+\S*broker\S*\s+import)',
            re.IGNORECASE | re.MULTILINE,
        )
        for f in new_files:
            if f.exists():
                content = f.read_text(encoding="utf-8")
                matches = broker_import_re.findall(content)
                assert len(matches) == 0, (
                    f"{f.name} has broker import: {matches[0]!r}" if matches else ""
                )

    def test_audit_report_generated(self, paper_config_fixture: dict) -> None:
        """generate_audit_report produces SAFE for safe inputs."""
        broker_check = {"ok": True, "violations": []}
        paper_check = assert_paper_mode(paper_config_fixture)
        report = generate_audit_report(broker_check, paper_check)
        assert report["verdict"] == "SAFE"
        assert "SAFE" in report["summary"]


# ── Tests: Integration End-to-End ──────────────────────────────────

class TestOverloadedPortfolioValidation:
    """Full validation of overloaded portfolio — expects VIOLATIONS."""

    def test_overloaded_full_validate(
        self, overloaded_portfolio_fixture: dict
    ) -> None:
        """8-slot overloaded portfolio must produce VIOLATIONS verdict."""
        result = validate_portfolio(overloaded_portfolio_fixture, max_slots=3)

        assert result["verdict"] == "VIOLATIONS"

        # Slot count violation
        assert result["checks"]["slot_count"]["ok"] is False
        assert result["checks"]["slot_count"]["count"] == 8

        # Duplicate violations
        assert result["checks"]["duplicates"]["ok"] is False
        dupes = result["checks"]["duplicates"]["found"]
        assert len(dupes) >= 1  # GAZP ft_bband_rsi ×4

        # Scorecard computed
        sc = result["scorecard"]
        assert sc["total_slots"] == 8
        assert sc["total_open_positions"] == 2
        assert sc["total_pnl_rub"] != 0.0

    def test_overloaded_with_guard(
        self,
        overloaded_portfolio_fixture: dict,
        paper_config_fixture: dict,
    ) -> None:
        """Combine validator + guard for full safety audit."""
        # Validate portfolio
        portfolio_result = validate_portfolio(overloaded_portfolio_fixture, max_slots=3)
        assert portfolio_result["verdict"] == "VIOLATIONS"

        # Run guard checks
        broker_check = {"ok": True, "violations": []}
        paper_check = assert_paper_mode(paper_config_fixture)

        # Generate audit report
        report = generate_audit_report(
            broker_check, paper_check, portfolio_check=portfolio_result
        )
        assert report["verdict"] == "UNSAFE"  # because portfolio has violations

    def test_clean_portfolio_passes_all_checks(
        self,
        paper_config_fixture: dict,
    ) -> None:
        """Clean 3-slot portfolio + paper config = SAFE audit."""
        clean_portfolio = {
            "slots": {
                "s1": {"ticker": "LKOH", "strategy": "vwap_reversion", "contracts": 1, "pnl_rub": 100.0, "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 42000.0}},
                "s2": {"ticker": "GAZP", "strategy": "ft_bband_rsi", "contracts": 1, "pnl_rub": 50.0, "open_position": None},
                "s3": {"ticker": "SBER", "strategy": "mean_reversion", "contracts": 1, "pnl_rub": -20.0, "open_position": None},
            },
            "peak_equity": 21281.0,
            "halted": False,
            "halt_reason": None,
        }
        portfolio_result = validate_portfolio(clean_portfolio, max_slots=3)
        assert portfolio_result["verdict"] == "PASS"

        broker_check = {"ok": True, "violations": []}
        paper_check = assert_paper_mode(paper_config_fixture)
        report = generate_audit_report(
            broker_check, paper_check, portfolio_check=portfolio_result
        )
        assert report["verdict"] == "SAFE"
