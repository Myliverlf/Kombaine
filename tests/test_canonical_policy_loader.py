"""Tests for canonical_policy_loader — the single source of truth for
qualification thresholds and safety guardrails.

T01-T10 cover the exact requirements specified in the task.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Ensure project root is on sys.path (mirrors conftest.py)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core import canonical_policy_loader as loader  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset module-level cache before each test so isolation is guaranteed."""
    loader._policy_cache = None
    loader._policy_raw_bytes = None
    yield
    loader._policy_cache = None
    loader._policy_raw_bytes = None


# ---------------------------------------------------------------------------
# T01: mode=paper
# ---------------------------------------------------------------------------
class TestT01_ModeIsPaper:
    def test_mode_is_paper(self):
        """config.json must declare mode='paper'."""
        cfg = loader._load_config_json()
        assert cfg["mode"] == "paper"


# ---------------------------------------------------------------------------
# T02: paper_first=true
# ---------------------------------------------------------------------------
class TestT02_PaperFirst:
    def test_paper_first_is_true(self):
        """config.json must declare paper_first=true."""
        cfg = loader._load_config_json()
        assert cfg["paper_first"] is True


# ---------------------------------------------------------------------------
# T03: canonical policy loads
# ---------------------------------------------------------------------------
class TestT03_PolicyLoads:
    def test_policy_loads_successfully(self):
        """get_qualification_policy() returns a non-empty dict."""
        policy = loader.get_qualification_policy()
        assert isinstance(policy, dict)
        assert "thresholds" in policy
        assert "version" in policy

    def test_policy_has_expected_top_level_keys(self):
        policy = loader.get_qualification_policy()
        for key in ("policy_id", "version", "thresholds", "cost_model", "stages"):
            assert key in policy, f"Missing top-level key: {key}"


# ---------------------------------------------------------------------------
# T04: invalid/missing policy fails closed
# ---------------------------------------------------------------------------
class TestT04_FailClosed:
    def test_missing_policy_raises_valueerror(self, tmp_path):
        """If the policy JSON is absent, a ValueError is raised."""
        loader._policy_cache = None
        loader._policy_raw_bytes = None
        with mock.patch.object(loader, "_POLICY_PATH", tmp_path / "nonexistent.json"):
            with pytest.raises(ValueError, match="not found"):
                loader._load_policy()

    def test_invalid_json_raises_valueerror(self, tmp_path):
        """If the policy file contains invalid JSON, a ValueError is raised."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{{ not valid json }}")
        loader._policy_cache = None
        loader._policy_raw_bytes = None
        with mock.patch.object(loader, "_POLICY_PATH", bad_file):
            with pytest.raises(ValueError, match="invalid"):
                loader._load_policy()

    def test_missing_thresholds_raises_valueerror(self, tmp_path):
        """If thresholds section is absent, a ValueError is raised."""
        incomplete = tmp_path / "incomplete.json"
        incomplete.write_text(json.dumps({"version": "1.0.0"}))
        loader._policy_cache = None
        loader._policy_raw_bytes = None
        with mock.patch.object(loader, "_POLICY_PATH", incomplete):
            with pytest.raises(ValueError, match="thresholds"):
                loader._load_policy()


# ---------------------------------------------------------------------------
# T05: get_threshold('min_trades') == 8
# ---------------------------------------------------------------------------
class TestT05_MinTrades:
    def test_min_trades(self):
        assert loader.get_threshold("min_trades") == 8


# ---------------------------------------------------------------------------
# T06: get_threshold('min_sharpe') == 0.3
# ---------------------------------------------------------------------------
class TestT06_MinSharpe:
    def test_min_sharpe(self):
        assert loader.get_threshold("min_sharpe") == 0.3


# ---------------------------------------------------------------------------
# T07: get_threshold('min_profit_factor') == 1.05
# ---------------------------------------------------------------------------
class TestT07_MinProfitFactor:
    def test_min_profit_factor(self):
        assert loader.get_threshold("min_profit_factor") == 1.05


# ---------------------------------------------------------------------------
# T08: get_threshold('max_drawdown_pct') == 25.0
# ---------------------------------------------------------------------------
class TestT08_MaxDrawdown:
    def test_max_drawdown_pct(self):
        assert loader.get_threshold("max_drawdown_pct") == 25.0


# ---------------------------------------------------------------------------
# T09: policy_hash is deterministic
# ---------------------------------------------------------------------------
class TestT09_HashDeterministic:
    def test_hash_deterministic(self):
        h1 = loader.policy_hash()
        h2 = loader.policy_hash()
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_hash_matches_file(self):
        """policy_hash() must match independent SHA-256 of the file."""
        import hashlib
        expected = hashlib.sha256(
            loader._POLICY_PATH.read_bytes()
        ).hexdigest()
        assert loader.policy_hash() == expected


# ---------------------------------------------------------------------------
# T10: policy_version == '1.0.0'
# ---------------------------------------------------------------------------
class TestT10_Version:
    def test_version(self):
        assert loader.policy_version() == "1.0.0"


# ---------------------------------------------------------------------------
# Additional: verify_safety_guardrails integration
# ---------------------------------------------------------------------------
class TestSafetyGuardrails:
    def test_guardrails_pass_on_clean_config(self):
        """verify_safety_guardrails() succeeds with current config.json."""
        loader.verify_safety_guardrails()  # should not raise

    def test_guardrails_reject_non_paper_mode(self):
        """If mode != 'paper', guardrails must fail closed."""
        fake_cfg = {"mode": "live", "paper_first": True}
        with mock.patch.object(loader, "_load_config_json", return_value=fake_cfg):
            with pytest.raises(ValueError, match="mode.*must be 'paper'"):
                loader.verify_safety_guardrails()

    def test_guardrails_reject_paper_first_false(self):
        """If paper_first=false, guardrails must fail closed."""
        fake_cfg = {"mode": "paper", "paper_first": False}
        with mock.patch.object(loader, "_load_config_json", return_value=fake_cfg):
            with pytest.raises(ValueError, match="paper_first"):
                loader.verify_safety_guardrails()

    def test_guardrails_reject_live_execute_enabled(self):
        """If LIVE_EXECUTE is set to something other than 'denied', reject."""
        fake_cfg = {"mode": "paper", "paper_first": True, "LIVE_EXECUTE": "enabled"}
        with mock.patch.object(loader, "_load_config_json", return_value=fake_cfg):
            with pytest.raises(ValueError, match="LIVE_EXECUTE"):
                loader.verify_safety_guardrails()

    def test_guardrails_accept_live_execute_absent(self):
        """If LIVE_EXECUTE is absent, that is equivalent to 'denied'."""
        fake_cfg = {"mode": "paper", "paper_first": True}
        with mock.patch.object(loader, "_load_config_json", return_value=fake_cfg):
            loader.verify_safety_guardrails()  # should not raise

    def test_guardrails_accept_live_execute_denied(self):
        """If LIVE_EXECUTE='denied', that is accepted."""
        fake_cfg = {"mode": "paper", "paper_first": True, "LIVE_EXECUTE": "denied"}
        with mock.patch.object(loader, "_load_config_json", return_value=fake_cfg):
            loader.verify_safety_guardrails()  # should not raise


# ---------------------------------------------------------------------------
# Additional: get_cost_model, get_stages, unknown threshold
# ---------------------------------------------------------------------------
class TestCostModelAndStages:
    def test_get_cost_model(self):
        cm = loader.get_cost_model()
        assert isinstance(cm, dict)
        assert "commission_per_contract" in cm
        assert cm["commission_per_contract"] == 5.0

    def test_get_stages(self):
        stages = loader.get_stages()
        assert isinstance(stages, dict)
        assert "PAPER_ADMISSION_READY" in stages
        assert "LIVE_CANDIDATE" in stages

    def test_unknown_threshold_raises_keyerror(self):
        with pytest.raises(KeyError, match="Unknown threshold"):
            loader.get_threshold("nonexistent_threshold")


# ---------------------------------------------------------------------------
# Additional: initialize() end-to-end
# ---------------------------------------------------------------------------
class TestInitialize:
    def test_initialize_success(self):
        """initialize() loads policy + passes guardrails."""
        policy = loader.initialize()
        assert isinstance(policy, dict)
        assert policy["version"] == "1.0.0"
