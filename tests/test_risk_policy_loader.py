"""
Tests for risk_policy_loader.py — LIVE_RISK_V1 policy enforcement.

Test IDs:
  T26  LIVE_RISK_V1 mapping complete
  T27  risk_per_trade aligned (0.25%)
  T28  gross_exposure aligned (10%)
  T29  single_position aligned (1)
  T30  max_positions aligned (1)
  T31  no leverage enforced
  T32  no shorting enforced
  T33  no averaging down/pyramiding
  T34  risk_policy_hash propagates
  T58  short candidate blocked
  T59  leverage candidate blocked
  T60  >1 concurrent position blocked
  T62  missing risk identity blocked
  T01  mode=paper verified
  T04  missing policy fails closed
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Project path setup ───────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
import risk_policy_loader as rpl


# ── Helpers ──────────────────────────────────────────────────────────────────
def _fresh_load():
    """Reset the module cache so next call re-reads the file."""
    rpl._policy_cache = None
    rpl._policy_raw_bytes = None


def _valid_policy_dict() -> dict:
    return {
        "policy_id": "LIVE_RISK_V1",
        "version": "1.0.0",
        "max_risk_per_trade_pct": 0.25,
        "max_gross_exposure_pct": 10.0,
        "max_concurrent_live_positions": 1,
        "shorting_allowed": False,
        "leverage_allowed": False,
        "averaging_down_allowed": False,
        "pyramiding_allowed": False,
    }


def _good_candidate() -> dict:
    return {
        "side": "long",
        "leverage": False,
        "risk_per_trade_pct": 0.20,
        "concurrent_live_positions": 1,
        "averaging_down": False,
        "pyramiding": False,
        "risk_identity": "test_identity_001",
    }


# =====================================================================
# T26: LIVE_RISK_V1 mapping complete
# =====================================================================
class TestT26Mapping:
    def test_policy_loads_all_required_keys(self):
        policy = rpl.get_risk_policy()
        required = [
            "policy_id",
            "version",
            "max_risk_per_trade_pct",
            "max_gross_exposure_pct",
            "max_concurrent_live_positions",
            "shorting_allowed",
            "leverage_allowed",
            "averaging_down_allowed",
            "pyramiding_allowed",
        ]
        for key in required:
            assert key in policy, f"Missing required key: {key}"

    def test_policy_id_is_live_risk_v1(self):
        assert rpl.get_risk_policy()["policy_id"] == "LIVE_RISK_V1"


# =====================================================================
# T27: risk_per_trade aligned (0.25%)
# =====================================================================
class TestT27RiskPerTrade:
    def test_max_risk_per_trade_pct(self):
        assert rpl.get_risk_policy()["max_risk_per_trade_pct"] == 0.25


# =====================================================================
# T28: gross_exposure aligned (10%)
# =====================================================================
class TestT28GrossExposure:
    def test_max_gross_exposure_pct(self):
        assert rpl.get_risk_policy()["max_gross_exposure_pct"] == 10.0


# =====================================================================
# T29: single_position aligned (1)
# =====================================================================
class TestT29SinglePosition:
    def test_max_concurrent_live_positions(self):
        assert rpl.get_risk_policy()["max_concurrent_live_positions"] == 1


# =====================================================================
# T30: max_positions aligned (1)
# =====================================================================
class TestT30MaxPositions:
    def test_max_positions_is_one(self):
        p = rpl.get_risk_policy()
        assert p["max_concurrent_live_positions"] == 1


# =====================================================================
# T31: no leverage enforced
# =====================================================================
class TestT31NoLeverage:
    def test_leverage_disallowed(self):
        assert rpl.get_risk_policy()["leverage_allowed"] is False

    def test_leverage_candidate_rejected(self):
        cand = _good_candidate()
        cand["leverage"] = True
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("leverage" in v.lower() for v in result["violations"])


# =====================================================================
# T32: no shorting enforced
# =====================================================================
class TestT32NoShorting:
    def test_shorting_disallowed(self):
        assert rpl.get_risk_policy()["shorting_allowed"] is False

    def test_short_candidate_rejected(self):
        cand = _good_candidate()
        cand["side"] = "short"
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("short" in v.lower() for v in result["violations"])


# =====================================================================
# T33: no averaging down/pyramiding
# =====================================================================
class TestT33NoAveragingPyramiding:
    def test_averaging_down_disallowed(self):
        assert rpl.get_risk_policy()["averaging_down_allowed"] is False

    def test_pyramiding_disallowed(self):
        assert rpl.get_risk_policy()["pyramiding_allowed"] is False

    def test_averaging_down_candidate_rejected(self):
        cand = _good_candidate()
        cand["averaging_down"] = True
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("averaging" in v.lower() for v in result["violations"])

    def test_pyramiding_candidate_rejected(self):
        cand = _good_candidate()
        cand["pyramiding"] = True
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("pyramiding" in v.lower() for v in result["violations"])


# =====================================================================
# T34: risk_policy_hash propagates
# =====================================================================
class TestT34HashPropagation:
    def test_hash_is_sha256_hex(self):
        h = rpl.risk_policy_hash()
        assert len(h) == 64
        int(h, 16)  # raises if not valid hex

    def test_hash_matches_raw_file(self):
        raw = rpl._POLICY_PATH.read_bytes()
        expected = hashlib.sha256(raw).hexdigest()
        assert rpl.risk_policy_hash() == expected

    def test_hash_stable_across_calls(self):
        h1 = rpl.risk_policy_hash()
        h2 = rpl.risk_policy_hash()
        assert h1 == h2


# =====================================================================
# T58: short candidate blocked
# =====================================================================
class TestT58ShortBlocked:
    def test_short_side_blocked(self):
        cand = _good_candidate()
        cand["side"] = "short"
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("short" in v.lower() for v in result["violations"])


# =====================================================================
# T59: leverage candidate blocked
# =====================================================================
class TestT59LeverageBlocked:
    def test_leverage_true_blocked(self):
        cand = _good_candidate()
        cand["leverage"] = True
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("leverage" in v.lower() for v in result["violations"])


# =====================================================================
# T60: >1 concurrent position blocked
# =====================================================================
class TestT60MultiPositionBlocked:
    def test_two_concurrent_positions_blocked(self):
        cand = _good_candidate()
        cand["concurrent_live_positions"] = 2
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("concurrent" in v.lower() for v in result["violations"])


# =====================================================================
# T62: missing risk identity blocked
# =====================================================================
class TestT62MissingIdentityBlocked:
    def test_no_risk_identity_blocked(self):
        cand = _good_candidate()
        cand.pop("risk_identity", None)
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("risk_identity" in v.lower() for v in result["violations"])

    def test_empty_risk_identity_blocked(self):
        cand = _good_candidate()
        cand["risk_identity"] = ""
        result = rpl.check_risk_qualification(cand)
        assert result["passed"] is False
        assert any("risk_identity" in v.lower() for v in result["violations"])


# =====================================================================
# T01: mode=paper verified
# =====================================================================
class TestT01ModePaper:
    def test_config_mode_is_paper(self):
        cfg_path = rpl._PROJECT_ROOT / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            assert cfg.get("mode") == "paper", (
                f"config.json mode={cfg.get('mode')}, expected 'paper'"
            )


# =====================================================================
# T04: missing policy fails closed
# =====================================================================
class TestT04MissingPolicyFailClosed:
    def test_missing_file_raises(self, tmp_path: Path):
        _fresh_load()
        fake_path = tmp_path / "missing_risk.json"
        with patch.object(rpl, "_POLICY_PATH", fake_path):
            with pytest.raises(FileNotFoundError, match="FAIL-CLOSED"):
                rpl.get_risk_policy()

    def test_corrupt_json_raises(self, tmp_path: Path):
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("NOT VALID JSON {{{")
        _fresh_load()
        with patch.object(rpl, "_POLICY_PATH", corrupt):
            with pytest.raises(ValueError, match="FAIL-CLOSED"):
                rpl.get_risk_policy()

    def test_missing_policy_id_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"policy_id": "WRONG"}))
        _fresh_load()
        with patch.object(rpl, "_POLICY_PATH", bad):
            with pytest.raises(ValueError, match="FAIL-CLOSED"):
                rpl.get_risk_policy()

    def test_missing_required_key_raises(self, tmp_path: Path):
        bad = tmp_path / "incomplete.json"
        bad.write_text(json.dumps({"policy_id": "LIVE_RISK_V1"}))
        _fresh_load()
        with patch.object(rpl, "_POLICY_PATH", bad):
            with pytest.raises(ValueError, match="FAIL-CLOSED"):
                rpl.get_risk_policy()


# =====================================================================
# Bonus: good candidate passes
# =====================================================================
class TestGoodCandidatePasses:
    def test_valid_candidate_passes(self):
        result = rpl.check_risk_qualification(_good_candidate())
        assert result["passed"] is True
        assert result["violations"] == []
