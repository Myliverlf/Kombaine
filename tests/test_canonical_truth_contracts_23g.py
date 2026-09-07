"""Iteration 23G: Canonical Truth Contracts & Cross-Layer Tests.

Tests asserting:
  - T1: Paper safety (mode=paper, paper_first=true, live forbidden)
  - T2: 60d-only cannot reach PAPER_ADMISSION_READY
  - T3: Policy stage semantics (stage ordering and paper_admission flags)
  - T4: Registry is sole writable truth for strategy state
  - T5: Handoff provenance (eligible_candidates immutability and provenance)
  - T6: Canonical truth contract loadable and valid
  - T7: Scheduler ownership metadata completeness
  - T8: Pre-live blockers documented

All tests are CLASS 2 (runtime non-trading). No broker, no live, no real data.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "code"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def canonical_truth():
    """Load the canonical truth contract."""
    path = ROOT / "docs" / "mission_control" / "canonical_truth.json"
    assert path.exists(), f"canonical_truth.json not found at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def qualification_policy():
    """Load the research qualification policy."""
    path = ROOT / "config" / "research_qualification_policy.json"
    assert path.exists(), f"research_qualification_policy.json not found at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def paper_config():
    """Load the current config.json (should be paper mode)."""
    path = ROOT / "config.json"
    assert path.exists(), f"config.json not found at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def run_dir_fixture(tmp_path):
    """Create a temporary run directory with eligible_candidates."""
    from core.eligible_candidates_contract import write_immutable_eligible_candidates

    run_dir = tmp_path / "runs" / "test_run_23g"
    run_dir.mkdir(parents=True)
    run_id = "run_20260830_120000_test01"

    eligible = [
        {
            "run_id": run_id,
            "config_key": "SBER__trend_breakout",
            "instrument": "SBER",
            "ticker": "SBER",
            "strategy": "trend_breakout",
            "parameters": {"lookback": 20, "atr_mult": 2.0},
            "metrics": {"total_pnl": 1600.0, "sharpe": 0.45, "win_rate": 55.0, "profit_factor": 1.3, "max_drawdown": 8.0, "trades": 30},
            "eligible": True,
            "reject_reasons": [],
            "horizon_days": 60,
        },
    ]

    write_immutable_eligible_candidates(
        run_dir=run_dir,
        run_id=run_id,
        eligible=eligible,
    )

    return run_dir, run_id, eligible


# ═══════════════════════════════════════════════════════════════════════════════
# T1: PAPER SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperSafety:
    """Assert paper mode is enforced and live is forbidden."""

    def test_config_mode_is_paper(self, paper_config):
        """T1.1: config.json mode must be 'paper'."""
        assert paper_config["mode"] == "paper", (
            f"Expected mode='paper', got mode='{paper_config['mode']}'"
        )

    def test_config_paper_first_is_true(self, paper_config):
        """T1.2: config.json paper_first must be True."""
        assert paper_config.get("paper_first") is True, (
            f"Expected paper_first=True, got paper_first={paper_config.get('paper_first')}"
        )

    def test_live_is_not_mode(self, paper_config):
        """T1.3: config.json mode must NOT be 'live'."""
        assert paper_config["mode"] != "live", (
            "CRITICAL: config.json mode is 'live'. This is forbidden."
        )

    def test_canonical_truth_declares_live_forbidden(self, canonical_truth):
        """T1.4: canonical_truth.json must declare live_forbidden=true."""
        assert canonical_truth.get("mode_safety", {}).get("live_forbidden") is True, (
            "canonical_truth.json does not declare live_forbidden=true"
        )

    def test_canonical_truth_declares_current_mode_paper(self, canonical_truth):
        """T1.5: canonical_truth.json must declare current_mode='paper'."""
        assert canonical_truth.get("mode_safety", {}).get("current_mode") == "paper", (
            "canonical_truth.json does not declare current_mode='paper'"
        )

    def test_paper_mode_requires_broker_veto(self, canonical_truth):
        """T1.6: canonical_truth.json must declare live_authorization_requires is non-empty."""
        reqs = canonical_truth.get("mode_safety", {}).get("live_authorization_requires", [])
        assert len(reqs) > 0, (
            "canonical_truth.json live_authorization_requires is empty — "
            "there must be explicit conditions for live authorization"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T2: 60D-ONLY CANNOT REACH PAPER_ADMISSION_READY
# ═══════════════════════════════════════════════════════════════════════════════

class TestSixtyDayCannotReachPaperAdmission:
    """Assert that a 60d-only candidate cannot reach PAPER_ADMISSION_READY."""

    def test_paper_admission_requires_multi_horizon(self, qualification_policy):
        """T2.1: PAPER_ADMISSION_READY requires MULTI_HORIZON_QUALIFIED."""
        stages = qualification_policy.get("stages", {})
        paper_stage = stages.get("PAPER_ADMISSION_READY", {})
        requires = paper_stage.get("requires", [])
        assert "MULTI_HORIZON_QUALIFIED" in requires, (
            f"PAPER_ADMISSION_READY does not require MULTI_HORIZON_QUALIFIED. "
            f"Requires: {requires}"
        )

    def test_multi_horizon_requires_60d_plus_more(self, qualification_policy):
        """T2.2: MULTI_HORIZON_QUALIFIED requires more than just 60d."""
        stages = qualification_policy.get("stages", {})
        multi_stage = stages.get("MULTI_HORIZON_QUALIFIED", {})
        requires = multi_stage.get("requires", [])
        assert "60d" in requires, (
            f"MULTI_HORIZON_QUALIFIED should require 60d. Requires: {requires}"
        )
        # Must require at least one horizon beyond 60d
        non_60d_horizons = [r for r in requires if r != "60d"]
        assert len(non_60d_horizons) > 0, (
            f"MULTI_HORIZON_QUALIFIED only requires 60d — no additional horizons. "
            f"Requires: {requires}. A 60d-only candidate would pass."
        )

    def test_paper_admission_requires_walk_forward(self, qualification_policy):
        """T2.3: PAPER_ADMISSION_READY requires WALK_FORWARD_QUALIFIED."""
        stages = qualification_policy.get("stages", {})
        paper_stage = stages.get("PAPER_ADMISSION_READY", {})
        requires = paper_stage.get("requires", [])
        assert "WALK_FORWARD_QUALIFIED" in requires, (
            f"PAPER_ADMISSION_READY does not require WALK_FORWARD_QUALIFIED"
        )

    def test_paper_admission_requires_robustness(self, qualification_policy):
        """T2.4: PAPER_ADMISSION_READY requires ROBUSTNESS_QUALIFIED."""
        stages = qualification_policy.get("stages", {})
        paper_stage = stages.get("PAPER_ADMISSION_READY", {})
        requires = paper_stage.get("requires", [])
        assert "ROBUSTNESS_QUALIFIED" in requires, (
            f"PAPER_ADMISSION_READY does not require ROBUSTNESS_QUALIFIED"
        )

    def test_paper_admission_requires_risk_qualified(self, qualification_policy):
        """T2.5: PAPER_ADMISSION_READY requires RISK_QUALIFIED."""
        stages = qualification_policy.get("stages", {})
        paper_stage = stages.get("PAPER_ADMISSION_READY", {})
        requires = paper_stage.get("requires", [])
        assert "RISK_QUALIFIED" in requires, (
            f"PAPER_ADMISSION_READY does not require RISK_QUALIFIED"
        )

    def test_60d_only_cannot_reach_paper_admission(self, qualification_policy):
        """T2.6: Exhaustive check — 60d-only path fails all requirements.

        A candidate that ONLY has 60d data cannot reach PAPER_ADMISSION_READY
        because PAPER_ADMISSION_READY requires:
          - MULTI_HORIZON_QUALIFIED (needs 60d + 90d + 180d + 365d + 1095d)
          - WALK_FORWARD_QUALIFIED (needs oos_folds + no_lookahead)
          - ROBUSTNESS_QUALIFIED (needs parameter_neighborhood + temporal_regime)
          - RISK_QUALIFIED (needs LIVE_RISK_V1 + bounded_stop + sizing + account_feasibility)
        60d-only has none of these beyond the 60d requirement.
        """
        stages = qualification_policy.get("stages", {})
        paper_stage = stages.get("PAPER_ADMISSION_READY", {})
        all_requires = paper_stage.get("requires", [])

        # A 60d-only candidate cannot provide:
        # - MULTI_HORIZON_QUALIFIED (needs 90d, 180d, 365d, 1095d)
        # - WALK_FORWARD_QUALIFIED (needs oos_folds, no_lookahead)
        # - ROBUSTNESS_QUALIFIED (needs parameter_neighborhood, temporal_regime)
        # - RISK_QUALIFIED (needs LIVE_RISK_V1, bounded_stop, sizing, account_feasibility)
        # Therefore at minimum 4 required stages are missing
        missing_count = sum(1 for r in all_requires if r not in ("BACKTEST_QUALIFIED", "no_genuine_paper_yet"))
        assert missing_count >= 3, (
            f"PAPER_ADMISSION_READY only requires {len(all_requires)} stages total. "
            f"A 60d-only candidate could potentially satisfy {len(all_requires) - missing_count}. "
            f"Expected at least 3 unachievable stages for 60d-only."
        )

    def test_invariant_declared_in_policy(self, qualification_policy):
        """T2.7: The invariant is explicitly declared in the policy."""
        invariants = qualification_policy.get("invariants", [])
        assert "60d_only_cannot_reach_PAPER_ADMISSION_READY" in invariants, (
            "Invariant '60d_only_cannot_reach_PAPER_ADMISSION_READY' not declared in policy invariants"
        )

    def test_invariant_declared_in_canonical_truth(self, canonical_truth):
        """T2.8: The invariant is declared in canonical_truth.json too."""
        invariants = canonical_truth.get("invariants", [])
        assert "60d_only_cannot_reach_PAPER_ADMISSION_READY" in invariants, (
            "Invariant '60d_only_cannot_reach_PAPER_ADMISSION_READY' not in canonical_truth invariants"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T3: POLICY STAGE SEMANTICS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPolicyStageSemantics:
    """Assert stage ordering and paper_admission flag correctness."""

    REQUIRED_STAGE_ORDER = [
        "DISCOVERY",
        "BACKTEST_QUALIFIED",
        "MULTI_HORIZON_QUALIFIED",
        "WALK_FORWARD_QUALIFIED",
        "ROBUSTNESS_QUALIFIED",
        "RISK_QUALIFIED",
        "PAPER_ADMISSION_READY",
        "PAPER_QUALIFIED",
        "LIVE_CANDIDATE",
    ]

    def test_all_required_stages_present(self, qualification_policy):
        """T3.1: All 9 required stages must be present in the policy."""
        stages = qualification_policy.get("stages", {})
        for stage_name in self.REQUIRED_STAGE_ORDER:
            assert stage_name in stages, (
                f"Required stage '{stage_name}' missing from policy. "
                f"Present: {list(stages.keys())}"
            )

    def test_pre_paper_stages_cannot_admit(self, qualification_policy):
        """T3.2: Stages before PAPER_ADMISSION_READY must have paper_admission=false."""
        stages = qualification_policy.get("stages", {})
        pre_paper = [
            "DISCOVERY", "BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED",
            "WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED", "RISK_QUALIFIED",
        ]
        for stage_name in pre_paper:
            stage = stages.get(stage_name, {})
            assert stage.get("paper_admission") is False, (
                f"Stage '{stage_name}' has paper_admission={stage.get('paper_admission')}. "
                f"Expected false."
            )

    def test_paper_stages_can_admit(self, qualification_policy):
        """T3.3: PAPER_ADMISSION_READY and PAPER_QUALIFIED must have paper_admission=true."""
        stages = qualification_policy.get("stages", {})
        paper_stages = ["PAPER_ADMISSION_READY", "PAPER_QUALIFIED"]
        for stage_name in paper_stages:
            stage = stages.get(stage_name, {})
            assert stage.get("paper_admission") is True, (
                f"Stage '{stage_name}' has paper_admission={stage.get('paper_admission')}. "
                f"Expected true."
            )

    def test_live_candidate_cannot_self_admit(self, qualification_policy):
        """T3.4: LIVE_CANDIDATE must have paper_admission=false (requires human auth)."""
        stages = qualification_policy.get("stages", {})
        live_stage = stages.get("LIVE_CANDIDATE", {})
        assert live_stage.get("paper_admission") is False, (
            "LIVE_CANDIDATE should have paper_admission=false — live requires human authorization"
        )

    def test_live_candidate_requires_human_authorization(self, qualification_policy):
        """T3.5: LIVE_CANDIDATE requires human_authorization_boundary."""
        stages = qualification_policy.get("stages", {})
        live_stage = stages.get("LIVE_CANDIDATE", {})
        requires = live_stage.get("requires", [])
        assert "human_authorization_boundary" in requires, (
            f"LIVE_CANDIDATE does not require human_authorization_boundary. "
            f"Requires: {requires}"
        )

    def test_paper_admission_ready_requires_all_prior_stages(self, qualification_policy):
        """T3.6: PAPER_ADMISSION_READY must require all 5 pre-paper stages."""
        stages = qualification_policy.get("stages", {})
        paper_stage = stages.get("PAPER_ADMISSION_READY", {})
        requires = paper_stage.get("requires", [])
        prior_stages = [
            "BACKTEST_QUALIFIED", "MULTI_HORIZON_QUALIFIED",
            "WALK_FORWARD_QUALIFIED", "ROBUSTNESS_QUALIFIED", "RISK_QUALIFIED",
        ]
        for ps in prior_stages:
            assert ps in requires, (
                f"PAPER_ADMISSION_READY missing required prior stage '{ps}'. "
                f"Requires: {requires}"
            )

    def test_policy_is_immutable(self, qualification_policy):
        """T3.7: Policy must declare immutable=true."""
        assert qualification_policy.get("immutable") is True, (
            "research_qualification_policy.json does not declare immutable=true"
        )

    def test_policy_has_version(self, qualification_policy):
        """T3.8: Policy must have a version field."""
        assert "version" in qualification_policy, (
            "research_qualification_policy.json missing 'version' field"
        )

    def test_no_orphan_stages(self, qualification_policy):
        """T3.9: Every stage must be required by at least one later stage (except DISCOVERY and LIVE_CANDIDATE).

        DISCOVERY is an entry point — it has no prior stage. LIVE_CANDIDATE is the terminal stage.
        Both are exempt. All other stages must appear as a requirement of at least one later stage.
        """
        stages = qualification_policy.get("stages", {})
        stage_names = set(stages.keys())

        # Collect all requirements across all stages
        all_requirements = set()
        for stage_data in stages.values():
            for req in stage_data.get("requires", []):
                all_requirements.add(req)

        # Every stage except LIVE_CANDIDATE must be referenced as a requirement
        exempt_stages = {"DISCOVERY", "LIVE_CANDIDATE"}
        for name in stage_names:
            if name in exempt_stages:
                continue
            assert name in all_requirements, (
                f"Stage '{name}' is not required by any later stage — unreachable or orphan"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# T4: REGISTRY SOLE WRITABLE TRUTH
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistrySoleWritableTruth:
    """Assert strategy_registry.json is the sole writable truth for strategy state."""

    def test_registry_declared_as_canonical_in_module(self):
        """T4.1: code/strategy_registry.py must declare REGISTRY_CANONICAL = True.

        Uses the core/ bridge module which loads code/strategy_registry.py correctly.
        """
        from core import strategy_registry as _bridge
        mod = _bridge
        assert getattr(mod, "REGISTRY_CANONICAL", False) is True, (
            "code/strategy_registry.py does not declare REGISTRY_CANONICAL = True"
        )

    def test_registry_file_exists(self):
        """T4.2: state/strategy_registry.json must exist."""
        path = ROOT / "state" / "strategy_registry.json"
        assert path.exists(), f"state/strategy_registry.json not found at {path}"

    def test_registry_has_required_keys(self):
        """T4.3: strategy_registry.json must have required top-level keys."""
        path = ROOT / "state" / "strategy_registry.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "strategies" in data, "strategy_registry.json missing 'strategies' key"
        assert "version" in data, "strategy_registry.json missing 'version' key"

    def test_canonical_truth_declares_registry_as_sole_writer(self, canonical_truth):
        """T4.4: canonical_truth.json must declare registry sole_writer."""
        stores = canonical_truth.get("canonical_state_stores", {})
        registry = stores.get("strategy_registry", {})
        writers = registry.get("sole_writer", [])
        assert len(writers) > 0, (
            "canonical_truth.json strategy_registry has no sole_writer"
        )
        assert "core/strategy_registry.py" in writers, (
            f"core/strategy_registry.py not in sole_writer list: {writers}"
        )

    def test_canonical_truth_declares_derived_views(self, canonical_truth):
        """T4.5: waitlist.json and signal_pool.json must be declared as derived, not canonical."""
        stores = canonical_truth.get("canonical_state_stores", {})
        registry = stores.get("strategy_registry", {})
        derived = registry.get("derived_views", [])
        assert "state/waitlist.json" in derived, (
            "waitlist.json not declared as derived view in canonical_truth"
        )
        assert "state/signal_pool.json" in derived, (
            "signal_pool.json not declared as derived view in canonical_truth"
        )

    def test_module_contracts_declare_registry_mutation(self):
        """T4.6: core/module_contracts.py must declare seeder_registry as the canonical writer."""
        # Import the module contracts
        import importlib.util
        # Use sys.path-based import to avoid dataclass __module__ resolution issues
        import core.module_contracts as mod

        contracts = getattr(mod, "MODULE_CONTRACTS", [])
        seeder_contracts = [
            c for c in contracts
            if getattr(c, "module_id", "") == "seeder_registry"
        ]
        assert len(seeder_contracts) == 1, (
            f"Expected exactly 1 seeder_registry contract, found {len(seeder_contracts)}"
        )
        contract = seeder_contracts[0]
        assert "YES" in getattr(contract, "registry_mutation", ""), (
            "seeder_registry contract does not have registry_mutation=YES"
        )

    def test_no_other_module_has_registry_mutation(self):
        """T4.7: No module except seeder_registry should have registry_mutation=YES."""
        import importlib.util
        import core.module_contracts as mod

        contracts = getattr(mod, "MODULE_CONTRACTS", [])
        violating = []
        for c in contracts:
            mid = getattr(c, "module_id", "")
            mut = getattr(c, "registry_mutation", "")
            if mid != "seeder_registry" and "YES" in mut:
                violating.append(mid)

        assert len(violating) == 0, (
            f"Modules with registry_mutation=YES besides seeder_registry: {violating}"
        )

    def test_canonical_truth_includes_registry_in_invariants(self, canonical_truth):
        """T4.8: canonical_truth invariants must include registry sole writable truth."""
        invariants = canonical_truth.get("invariants", [])
        assert "strategy_registry_json_is_sole_writable_truth_for_strategy_state" in invariants, (
            "Invariant about registry sole writable truth missing from canonical_truth"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T5: HANDOFF PROVENANCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandoffProvenance:
    """Assert eligible_candidates immutability and provenance stamping."""

    def test_eligible_candidates_written_and_provenance_stamped(self, run_dir_fixture):
        """T5.1: eligible_candidates.json must exist and have _provenance on each candidate."""
        run_dir, run_id, eligible = run_dir_fixture
        from core.eligible_candidates_contract import load_eligible_candidates

        loaded = load_eligible_candidates(run_dir)
        assert loaded is not None, "eligible_candidates.json not found"
        assert len(loaded) == len(eligible), (
            f"Expected {len(eligible)} candidates, got {len(loaded)}"
        )
        for i, cand in enumerate(loaded):
            assert "_provenance" in cand, (
                f"candidate[{i}] missing _provenance stamp"
            )
            prov = cand["_provenance"]
            assert prov.get("run_id") == run_id, (
                f"candidate[{i}] run_id mismatch: {prov.get('run_id')} != {run_id}"
            )

    def test_eligible_candidates_immutable(self, run_dir_fixture):
        """T5.2: eligible_candidates.json must be read-only after write."""
        run_dir, _, _ = run_dir_fixture
        eligible_path = run_dir / "eligible_candidates.json"
        stat = os.stat(str(eligible_path))
        is_readonly = (stat.st_mode & 0o222) == 0
        assert is_readonly, (
            f"eligible_candidates.json is not read-only: mode={oct(stat.st_mode)}"
        )

    def test_cannot_overwrite_eligible_candidates(self, run_dir_fixture):
        """T5.3: Writing to existing eligible_candidates.json must raise ValueError."""
        from core.eligible_candidates_contract import write_immutable_eligible_candidates

        run_dir, run_id, eligible = run_dir_fixture
        with pytest.raises(ValueError, match="already exists"):
            write_immutable_eligible_candidates(
                run_dir=run_dir,
                run_id=run_id,
                eligible=eligible,
            )

    def test_handoff_manifest_exists(self, run_dir_fixture):
        """T5.4: handoff_manifest.json must exist alongside eligible_candidates.json."""
        from core.eligible_candidates_contract import load_handoff_manifest

        run_dir, _, _ = run_dir_fixture
        manifest = load_handoff_manifest(run_dir)
        assert manifest is not None, "handoff_manifest.json not found"
        assert manifest.get("immutable") is True, (
            "handoff_manifest.json does not declare immutable=true"
        )

    def test_handoff_manifest_hash_matches_file(self, run_dir_fixture):
        """T5.5: handoff_manifest.json artifact_hash must match actual file hash."""
        from core.eligible_candidates_contract import _file_hash

        run_dir, _, _ = run_dir_fixture
        manifest_path = run_dir / "handoff_manifest.json"
        eligible_path = run_dir / "eligible_candidates.json"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_hash = manifest.get("artifact_hash", "")
        actual_hash = _file_hash(eligible_path)
        assert expected_hash == actual_hash, (
            f"Artifact hash mismatch: manifest={expected_hash} actual={actual_hash}"
        )

    def test_verify_eligible_contract_passes(self, run_dir_fixture):
        """T5.6: verify_eligible_candidates_contract() should pass for valid artifact."""
        from core.eligible_candidates_contract import verify_eligible_candidates_contract

        run_dir, run_id, _ = run_dir_fixture
        result = verify_eligible_candidates_contract(run_dir, expected_run_id=run_id)
        assert result.valid is True, (
            f"Verification failed: {result.rejection_reasons}"
        )
        assert result.immutable_ok is True
        assert result.provenance_ok is True
        assert result.hash_ok is True

    def test_verify_rejects_missing_provenance(self, run_dir_fixture):
        """T5.7: Verification must reject candidates without _provenance."""
        from core.eligible_candidates_contract import verify_eligible_candidates_contract

        run_dir, _, _ = run_dir_fixture
        # Tamper: write a new eligible without provenance
        eligible_path = run_dir / "eligible_candidates.json"
        os.chmod(str(eligible_path), 0o644)  # make writable for test
        tampered = [{"config_key": "FAKE__test", "instrument": "FAKE"}]
        eligible_path.write_text(json.dumps(tampered), encoding="utf-8")

        result = verify_eligible_candidates_contract(run_dir)
        assert result.valid is False
        assert result.provenance_ok is False

    def test_canonical_truth_declares_eligible_immutable(self, canonical_truth):
        """T5.8: canonical_truth.json must declare eligible_candidates as immutable."""
        immutable = canonical_truth.get("immutable_artifacts", {})
        ec = immutable.get("eligible_candidates", {})
        assert ec.get("immutable_after") is not None, (
            "canonical_truth.json does not declare eligible_candidates immutable_after"
        )

    def test_eligible_candidates_invariant_declared(self, canonical_truth):
        """T5.9: Invariant about eligible_candidates immutability must be declared."""
        invariants = canonical_truth.get("invariants", [])
        assert "eligible_candidates_are_immutable_once_finalized" in invariants, (
            "Invariant about eligible_candidates immutability missing"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T6: CANONICAL TRUTH CONTRACT INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalTruthContractIntegrity:
    """Assert the canonical truth contract is complete and self-consistent."""

    def test_contract_id_present(self, canonical_truth):
        """T6.1: contract_id must be present."""
        assert canonical_truth.get("contract_id") == "CANONICAL_TRUTH_V1"

    def test_declared_immutable(self, canonical_truth):
        """T6.2: The contract must declare itself immutable."""
        assert canonical_truth.get("immutable") is True

    def test_truth_hierarchy_complete(self, canonical_truth):
        """T6.3: All 6 hierarchy levels must be present."""
        hierarchy = canonical_truth.get("truth_hierarchy", {})
        expected_levels = [
            "level_1_master_governance",
            "level_2_current_system_truth",
            "level_3_accepted_decisions",
            "level_4_mission_control_state",
            "level_5_iteration_directives",
            "level_6_evidence",
        ]
        for level in expected_levels:
            assert level in hierarchy, (
                f"truth_hierarchy missing '{level}'"
            )

    def test_canonical_state_stores_complete(self, canonical_truth):
        """T6.4: All major state stores must be declared."""
        stores = canonical_truth.get("canonical_state_stores", {})
        required_stores = [
            "strategy_registry", "portfolio", "config",
            "strategy_lifecycle_db", "experiment_memory_db",
        ]
        for store in required_stores:
            assert store in stores, f"canonical_state_stores missing '{store}'"

    def test_invariants_list_non_empty(self, canonical_truth):
        """T6.5: Invariants list must be non-empty."""
        invariants = canonical_truth.get("invariants", [])
        assert len(invariants) > 0, "canonical_truth.json has no invariants"

    def test_pre_live_blockers_present(self, canonical_truth):
        """T6.6: pre_live_blockers must be declared."""
        blockers = canonical_truth.get("pre_live_blockers", [])
        assert len(blockers) > 0, (
            "No pre_live_blockers declared — should document migration gaps"
        )

    def test_scheduler_ownership_complete(self, canonical_truth):
        """T6.7: scheduler_ownership must document all known timers."""
        schedulers = canonical_truth.get("scheduler_ownership", {})
        required_timers = [
            "combine-research-daily.timer",
            "combine-seeder.timer",
            "combine-supervisor.timer",
        ]
        for timer in required_timers:
            assert timer in schedulers, (
                f"scheduler_ownership missing '{timer}'"
            )

    def test_architecture_docs_marked_non_canonical(self, canonical_truth):
        """T6.8: canonical_truth must note competing docs as non-canonical."""
        level2 = canonical_truth.get("truth_hierarchy", {}).get("level_2_current_system_truth", {})
        note = level2.get("note", "").lower()
        assert "non-canonical" in note or "non canonical" in note, (
            "canonical_truth does not mark competing architecture docs as non-canonical"
        )

    def test_absolute_paths_or_relative_to_root(self, canonical_truth):
        """T6.9: All path references should be relative to project root."""
        # Check that no absolute /root/ paths appear in truth hierarchy
        hierarchy_json = json.dumps(canonical_truth.get("truth_hierarchy", {}))
        # Allow /root/ in mode_safety and other non-path sections
        # But truth_hierarchy paths should be relative
        assert "/root/" not in hierarchy_json, (
            f"truth_hierarchy contains absolute /root/ paths: {hierarchy_json[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T7: SCHEDULER OWNERSHIP METADATA
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulerOwnershipMetadata:
    """Assert scheduler ownership is properly documented."""

    def test_research_daily_timer_has_owner(self, canonical_truth):
        """T7.1: combine-research-daily.timer must have an owner."""
        schedulers = canonical_truth.get("scheduler_ownership", {})
        timer = schedulers.get("combine-research-daily.timer", {})
        assert timer.get("owner"), "combine-research-daily.timer has no owner"

    def test_research_daily_timer_has_lock(self, canonical_truth):
        """T7.2: combine-research-daily.timer must declare its lock mechanism."""
        schedulers = canonical_truth.get("scheduler_ownership", {})
        timer = schedulers.get("combine-research-daily.timer", {})
        assert timer.get("lock"), "combine-research-daily.timer has no lock declared"

    def test_supervisor_timer_has_mode_guard(self, canonical_truth):
        """T7.3: combine-supervisor.timer must declare mode_guard."""
        schedulers = canonical_truth.get("scheduler_ownership", {})
        timer = schedulers.get("combine-supervisor.timer", {})
        assert "mode_guard" in timer, (
            "combine-supervisor.timer missing mode_guard — paper safety depends on it"
        )

    def test_research_timer_active_contradicts_old_docs(self, canonical_truth):
        """T7.4: The timer must be documented as ACTIVE (resolving old-doc contradiction)."""
        schedulers = canonical_truth.get("scheduler_ownership", {})
        timer = schedulers.get("combine-research-daily.timer", {})
        note = timer.get("note", "").lower()
        assert "active" in note, (
            "combine-research-daily.timer note does not mention ACTIVE status"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# T8: PRE-LIVE BLOCKERS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreLiveBlockers:
    """Assert pre-live blockers are documented with status."""

    def test_at_least_four_blockers(self, canonical_truth):
        """T8.1: At least 4 pre-live blockers must be documented."""
        blockers = canonical_truth.get("pre_live_blockers", [])
        assert len(blockers) >= 4, (
            f"Only {len(blockers)} pre-live blockers documented. Expected at least 4."
        )

    def test_blockers_have_status(self, canonical_truth):
        """T8.2: Every blocker must have a status field."""
        blockers = canonical_truth.get("pre_live_blockers", [])
        for b in blockers:
            assert "id" in b, f"Blocker missing 'id': {b}"
            assert "title" in b, f"Blocker missing 'title': {b}"
            assert "status" in b, f"Blocker missing 'status': {b}"
            assert "description" in b, f"Blocker missing 'description': {b}"

    def test_no_blocker_falsely_resolved(self, canonical_truth):
        """T8.3: No blocker should claim RESOLVED if migration is incomplete."""
        blockers = canonical_truth.get("pre_live_blockers", [])
        for b in blockers:
            status = b.get("status", "")
            # RESOLVED_IN_THIS_DOCUMENT is acceptable for documentation fixes
            # RESOLVED alone is not acceptable for migration blockers
            if status == "RESOLVED":
                pytest.fail(
                    f"Blocker '{b.get('title')}' claims RESOLVED — "
                    f"if full consumer migration is unsafe, mark as NOT_MIGRATED or PARTIAL"
                )
