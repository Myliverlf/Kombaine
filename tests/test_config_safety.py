"""Test Config Safety — guard against live orders and dangerous config.

3 pytest tests validating:
  1. config.json mode must be paper/dryrun/backtest (not "live")
  2. e2e_real_dryrun.py has no broker/tinkoff imports
  3. config risk params: max_slots<=3, contracts=1, RI excluded

Uses conftest fixtures. No live broker, no network.
"""
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))


class TestConfigModeSafety:
    """Config must not be in live mode for dry-run/testing."""

    def test_config_mode_paper_or_dryrun(self):
        """config.json mode must be paper, dryrun, or backtest — not live."""
        config_path = COMBINE_DIR / "config.json"
        assert config_path.exists(), "config.json not found"

        config = json.loads(config_path.read_text(encoding="utf-8"))
        mode = config.get("mode", "unknown")
        safe_modes = {"paper", "dryrun", "backtest", "test"}
        assert mode in safe_modes, (
            "config.json mode='%s' is DANGEROUS — must be one of %s. "
            "Live mode in test config risks real broker orders." % (mode, safe_modes)
        )

    def test_no_live_imports_in_e2e(self):
        """e2e_real_dryrun.py must not import broker/tinkoff/investpy."""
        e2e_path = CODE_DIR / "e2e_real_dryrun.py"
        if not e2e_path.exists():
            pytest.skip("e2e_real_dryrun.py not found")

        source = e2e_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_modules = {"tinkoff", "broker", "futures_lab", "investpy"}
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0].lower()
                    if top in forbidden_modules:
                        violations.append("import %s" % alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0].lower()
                    if top in forbidden_modules:
                        violations.append("from %s" % node.module)

        assert len(violations) == 0, (
            "Broker imports found in e2e_real_dryrun.py: %s" % violations
        )


class TestRiskParams:
    """Risk params: max_slots<=3, contracts=1, RI excluded."""

    def test_max_slots_and_contracts(self):
        """config risk params enforce max_slots<=3, contracts=1, RI excluded."""
        config_path = COMBINE_DIR / "config.json"
        assert config_path.exists(), "config.json not found"

        config = json.loads(config_path.read_text(encoding="utf-8"))
        risk = config.get("risk", {})
        excluded = config.get("excluded", [])

        max_slots = risk.get("max_slots", 0)
        max_contracts = risk.get("max_contracts_per_entry", 0)

        assert max_slots <= 3, (
            "max_slots=%d exceeds limit of 3" % max_slots
        )
        assert max_contracts == 1, (
            "max_contracts_per_entry=%d must be 1" % max_contracts
        )
        assert "RI" in excluded, (
            "RI must be in excluded list, got %s" % excluded
        )
