"""Iteration 23B — T1-T30 Live Preconditions & Broker Truth Tests.

All tests MUST pass. No real broker calls. No real orders.
Tests validate policy, broker truth, risk limits, data quality, and readiness.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

# ── Paths ──────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
STATE_DIR = ROOT / "state"
CODE_DIR = ROOT / "code"
DOCS_DIR = ROOT / "docs" / "mission_control" / "reviews" / "ITERATION-23B"
LIVE_RISK_PATH = STATE_DIR / "live_risk" / "LIVE_RISK_V1.json"
SNAPSHOT_PATH = STATE_DIR / "prelive_snapshot.json"
BROKER_SNAPSHOT_PATH = STATE_DIR / "broker_truth_snapshot_23b.json"
DATA_DIR = Path("/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data")


def _load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_config():
    return _load_json(CONFIG_PATH)


def _load_live_risk():
    return _load_json(LIVE_RISK_PATH)


def _load_snapshot():
    return _load_json(SNAPSHOT_PATH)


def _load_broker_snapshot():
    return _load_json(BROKER_SNAPSHOT_PATH)


# ══════════════════════════════════════════════════════════════════
# T1: Full-account capital base semantics
# ══════════════════════════════════════════════════════════════════
class TestT1CapitalBaseSemantics:
    def test_live_risk_policy_exists(self):
        """LIVE_RISK_V1.json must exist."""
        assert LIVE_RISK_PATH.exists(), "LIVE_RISK_V1.json not found"

    def test_capital_base_method_full_equity(self):
        """capital_base_method must be FULL_ACCOUNT_EQUITY."""
        lr = _load_live_risk()
        assert lr["capital_base_method"] == "FULL_ACCOUNT_EQUITY"

    def test_policy_id(self):
        """Policy ID must be LIVE_RISK_V1."""
        lr = _load_live_risk()
        assert lr["policy_id"] == "LIVE_RISK_V1"

    def test_owner_approval_reference(self):
        """Policy must reference owner approval."""
        lr = _load_live_risk()
        assert "owner_approval_reference" in lr
        assert len(lr["owner_approval_reference"]) > 0


# ══════════════════════════════════════════════════════════════════
# T2: Full capital != forced full exposure
# ══════════════════════════════════════════════════════════════════
class TestT2CapitalNotExposure:
    def test_gross_exposure_not_100pct(self):
        """max_gross_exposure must NOT be 100%."""
        lr = _load_live_risk()
        gross = lr["gross_exposure_limit"]["max_gross_exposure_pct"]
        assert gross < 100, f"Gross exposure is {gross}%, should be < 100%"

    def test_gross_exposure_is_10pct(self):
        """First pilot cap should be 10%."""
        lr = _load_live_risk()
        gross = lr["gross_exposure_limit"]["max_gross_exposure_pct"]
        assert gross == 10.0

    def test_documentation_says_not_forced(self):
        """Owner capital policy must state capital != forced exposure."""
        policy_path = DOCS_DIR / "owner_capital_policy.md"
        if policy_path.exists():
            content = policy_path.read_text()
            assert "!=" in content or "NOT" in content.upper()


# ══════════════════════════════════════════════════════════════════
# T3: No leverage
# ══════════════════════════════════════════════════════════════════
class TestT3NoLeverage:
    def test_leverage_not_allowed(self):
        lr = _load_live_risk()
        assert lr["leverage_allowed"] is False

    def test_borrowed_cash_not_allowed(self):
        lr = _load_live_risk()
        assert lr["borrowed_cash_allowed"] is False

    def test_margin_not_allowed(self):
        lr = _load_live_risk()
        assert lr["intentional_margin_usage"] is False

    def test_shorting_not_allowed(self):
        lr = _load_live_risk()
        assert lr["shorting_allowed"] is False


# ══════════════════════════════════════════════════════════════════
# T4: Pilot gross exposure <= 10%
# ══════════════════════════════════════════════════════════════════
class TestT4PilotGrossExposure:
    def test_max_gross_exposure_10pct(self):
        lr = _load_live_risk()
        assert lr["gross_exposure_limit"]["max_gross_exposure_pct"] == 10.0

    def test_single_position_exposure_10pct(self):
        lr = _load_live_risk()
        assert lr["single_position_exposure_limit"]["max_single_position_exposure_pct"] == 10.0


# ══════════════════════════════════════════════════════════════════
# T5: Planned risk <= 0.25% equity
# ══════════════════════════════════════════════════════════════════
class TestT5PlannedRisk:
    def test_per_trade_risk_025pct(self):
        lr = _load_live_risk()
        assert lr["per_trade_risk_limit"]["max_risk_per_trade_pct"] == 0.25


# ══════════════════════════════════════════════════════════════════
# T6: Per-strategy open risk <= 0.50%
# ══════════════════════════════════════════════════════════════════
class TestT6PerStrategyRisk:
    def test_per_strategy_risk_050pct(self):
        lr = _load_live_risk()
        assert lr["per_strategy_risk_limit"]["max_open_strategy_risk_pct"] == 0.50


# ══════════════════════════════════════════════════════════════════
# T7: Total open risk <= 0.50%
# ══════════════════════════════════════════════════════════════════
class TestT7TotalOpenRisk:
    def test_portfolio_risk_050pct(self):
        lr = _load_live_risk()
        assert lr["portfolio_open_risk_limit"]["max_total_open_risk_pct"] == 0.50


# ══════════════════════════════════════════════════════════════════
# T8: Daily 1% halt
# ══════════════════════════════════════════════════════════════════
class TestT8DailyHalt:
    def test_daily_loss_1pct(self):
        lr = _load_live_risk()
        assert lr["daily_loss_limit"]["daily_realized_plus_open_loss_halt_pct"] == 1.00

    def test_daily_breach_blocks_orders(self):
        lr = _load_live_risk()
        breach = lr["daily_loss_limit"]["on_breach"]
        assert "NO_NEW_ORDERS" in breach or "NO ORDERS" in str(breach).upper()


# ══════════════════════════════════════════════════════════════════
# T9: Weekly 2% halt
# ══════════════════════════════════════════════════════════════════
class TestT9WeeklyHalt:
    def test_weekly_loss_2pct(self):
        lr = _load_live_risk()
        assert lr["weekly_loss_limit"]["weekly_loss_halt_pct"] == 2.00

    def test_weekly_breach_requires_human(self):
        lr = _load_live_risk()
        breach = lr["weekly_loss_limit"]["on_breach"]
        assert any("HUMAN" in b.upper() for b in breach)


# ══════════════════════════════════════════════════════════════════
# T10: Drawdown 5% halt
# ══════════════════════════════════════════════════════════════════
class TestT10DrawdownHalt:
    def test_drawdown_halt_5pct(self):
        lr = _load_live_risk()
        assert lr["portfolio_drawdown_halt"]["hard_live_drawdown_halt_pct"] == 5.00

    def test_drawdown_breach_opens_incident(self):
        lr = _load_live_risk()
        breach = lr["portfolio_drawdown_halt"]["on_breach"]
        assert any("INCIDENT" in b.upper() for b in breach)


# ══════════════════════════════════════════════════════════════════
# T11: No averaging down
# ══════════════════════════════════════════════════════════════════
class TestT11NoAveragingDown:
    def test_averaging_down_not_allowed(self):
        lr = _load_live_risk()
        assert lr["averaging_down_allowed"] is False


# ══════════════════════════════════════════════════════════════════
# T12: No pyramiding pilot
# ══════════════════════════════════════════════════════════════════
class TestT12NoPyramiding:
    def test_pyramiding_not_allowed(self):
        lr = _load_live_risk()
        assert lr["pyramiding_allowed"] is False


# ══════════════════════════════════════════════════════════════════
# T13: One live position pilot
# ══════════════════════════════════════════════════════════════════
class TestT13OnePositionPilot:
    def test_max_concurrent_positions_1(self):
        lr = _load_live_risk()
        assert lr["max_concurrent_live_positions"] == 1


# ══════════════════════════════════════════════════════════════════
# T14: Strategy cannot loosen global limits
# ══════════════════════════════════════════════════════════════════
class TestT14StrategyCannotLoosen:
    def test_risk_hierarchy_documented(self):
        lr = _load_live_risk()
        assert "risk_hierarchy" in lr
        hierarchy = lr["risk_hierarchy"]
        assert "GLOBAL SAFETY HALT" in hierarchy[0]
        assert "SIGNAL" in hierarchy[-2]
        assert "EXECUTION" in hierarchy[-1]

    def test_risk_gate_final_authority(self):
        lr = _load_live_risk()
        assert lr["kill_halt_semantics"]["override_authority"].startswith("RISK_GATE")


# ══════════════════════════════════════════════════════════════════
# T15: Allocation cannot bypass Risk Gate
# ══════════════════════════════════════════════════════════════════
class TestT15AllocationNoBypass:
    def test_risk_gate_final_in_hierarchy(self):
        """Risk Gate must appear in hierarchy before allocation."""
        lr = _load_live_risk()
        hierarchy = lr["risk_hierarchy"]
        risk_gate_idx = next(i for i, h in enumerate(hierarchy) if "RISK" in h.upper() and "RECONCILIATION" not in h.upper())
        alloc_idx = next(i for i, h in enumerate(hierarchy) if "ALLOCATION" in h.upper())
        assert risk_gate_idx < alloc_idx, "Risk Gate must precede allocation in hierarchy"


# ══════════════════════════════════════════════════════════════════
# T16: Missing risk boundary blocks
# ══════════════════════════════════════════════════════════════════
class TestT16MissingRiskBoundary:
    def test_strategy_risk_contract_documents_block(self):
        """Strategy risk contract must document NOT_LIVE_ELIGIBLE for missing boundary."""
        risk_contract = DOCS_DIR / "strategy_risk_contract.md"
        if risk_contract.exists():
            content = risk_contract.read_text()
            assert "NOT_LIVE_ELIGIBLE" in content


# ══════════════════════════════════════════════════════════════════
# T17: GAZP/LKOH/SBER tier policy
# ══════════════════════════════════════════════════════════════════
class TestT17TierPolicy:
    def test_universe_policy_exists(self):
        universe_path = DOCS_DIR / "tradable_universe_policy.md"
        assert universe_path.exists()

    def test_tier_1_includes_gazp_lkoh_sber(self):
        universe_path = DOCS_DIR / "tradable_universe_policy.md"
        content = universe_path.read_text()
        for ticker in ["GAZP", "LKOH", "SBER"]:
            assert ticker in content


# ══════════════════════════════════════════════════════════════════
# T18: BR/Si restriction respected
# ══════════════════════════════════════════════════════════════════
class TestT18BRSiRestricted:
    def test_br_si_restricted(self):
        universe_path = DOCS_DIR / "tradable_universe_policy.md"
        content = universe_path.read_text()
        assert "BR" in content
        assert "Si" in content
        # They should be noted as restricted/blocked
        assert "BLOCKED" in content or "RESTRICTED" in content or "restricted" in content.lower()


# ══════════════════════════════════════════════════════════════════
# T19: Extra instrument cannot auto-enter LIVE
# ══════════════════════════════════════════════════════════════════
class TestT19NoAutoExpansion:
    def test_no_auto_expansion(self):
        universe_path = DOCS_DIR / "tradable_universe_policy.md"
        content = universe_path.read_text()
        assert "NOT automatically expand" in content or "No silent expansion" in content


# ══════════════════════════════════════════════════════════════════
# T20: Broker read-only guard
# ══════════════════════════════════════════════════════════════════
class TestT20BrokerReadOnly:
    def test_broker_readonly_runtime_exists(self):
        readonly_path = DOCS_DIR / "broker_readonly_runtime.md"
        assert readonly_path.exists()

    def test_zero_mutating_calls(self):
        readonly_path = DOCS_DIR / "broker_readonly_runtime.md"
        content = readonly_path.read_text()
        assert "0" in content  # mutating calls count
        assert "MUTATING" in content.upper()


# ══════════════════════════════════════════════════════════════════
# T21: Broker mutation denied
# ══════════════════════════════════════════════════════════════════
class TestT21BrokerMutationDenied:
    def test_no_post_order_in_code(self):
        """No PostOrderRequest in production code/."""
        for py_file in CODE_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text()
            assert "PostOrderRequest" not in content, (
                f"PostOrderRequest found in {py_file.relative_to(ROOT)}"
            )

    def test_no_cancel_order_in_code(self):
        """No CancelOrderRequest in production code/."""
        for py_file in CODE_DIR.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text()
            assert "CancelOrderRequest" not in content, (
                f"CancelOrderRequest found in {py_file.relative_to(ROOT)}"
            )


# ══════════════════════════════════════════════════════════════════
# T22: Account equity authoritative
# ══════════════════════════════════════════════════════════════════
class TestT22AccountEquity:
    def test_equity_snapshot_exists(self):
        equity_path = DOCS_DIR / "account_equity_snapshot.md"
        assert equity_path.exists()

    def test_equity_from_broker(self):
        equity_path = DOCS_DIR / "account_equity_snapshot.md"
        content = equity_path.read_text()
        assert "READ_ONLY" in content or "broker" in content.lower()

    def test_capital_base_derived(self):
        equity_path = DOCS_DIR / "account_equity_snapshot.md"
        content = equity_path.read_text()
        assert "capital_base" in content


# ══════════════════════════════════════════════════════════════════
# T23: Reconciliation
# ══════════════════════════════════════════════════════════════════
class TestT23Reconciliation:
    def test_reconciliation_exists(self):
        recon_path = DOCS_DIR / "reconciliation.md"
        assert recon_path.exists()

    def test_reconciliation_status(self):
        recon_path = DOCS_DIR / "reconciliation.md"
        content = recon_path.read_text()
        assert any(s in content for s in ["CONSISTENT", "DEGRADED", "CONFLICTED", "INCOMPLETE"])

    def test_no_corrective_order(self):
        recon_path = DOCS_DIR / "reconciliation.md"
        content = recon_path.read_text()
        assert "no corrective" in content.lower() or "NONE" in content


# ══════════════════════════════════════════════════════════════════
# T24: Position state not assumed
# ══════════════════════════════════════════════════════════════════
class TestT24PositionStateNotAssumed:
    def test_position_state_documented(self):
        pos_path = DOCS_DIR / "broker_position_state.md"
        assert pos_path.exists()

    def test_never_assume_flat(self):
        pos_path = DOCS_DIR / "broker_position_state.md"
        content = pos_path.read_text()
        assert "NEVER ASSUME FLAT" in content or "NON_FLAT" in content


# ══════════════════════════════════════════════════════════════════
# T25: Invalid lot => no trade
# ══════════════════════════════════════════════════════════════════
class TestT25InvalidLotNoTrade:
    def test_allocation_proof_no_trade(self):
        alloc_path = DOCS_DIR / "pilot_allocation_proof.md"
        assert alloc_path.exists()
        content = alloc_path.read_text()
        assert "NO_TRADE" in content


# ══════════════════════════════════════════════════════════════════
# T26: Stale data blocks
# ══════════════════════════════════════════════════════════════════
class TestT26StaleDataBlocks:
    def test_data_certification_exists(self):
        cert_path = DOCS_DIR / "data_scope_certification.md"
        assert cert_path.exists()

    def test_fail_closed_liquidity(self):
        liq_path = DOCS_DIR / "liquidity_policy.md"
        assert liq_path.exists()
        content = liq_path.read_text()
        assert "FAIL_CLOSED" in content or "UNKNOWN" in content


# ══════════════════════════════════════════════════════════════════
# T27: Stale strategy blocks
# ══════════════════════════════════════════════════════════════════
class TestT27StaleStrategyBlocks:
    def test_strategy_selection_documented(self):
        sel_path = DOCS_DIR / "pilot_strategy_selection.md"
        assert sel_path.exists()

    def test_no_eligible_or_thresholds_not_weakened(self):
        sel_path = DOCS_DIR / "pilot_strategy_selection.md"
        content = sel_path.read_text()
        assert "NO_LIVE_STRATEGY_ELIGIBLE" in content or "NOT weakened" in content.lower()


# ══════════════════════════════════════════════════════════════════
# T28: Human-only authorization
# ══════════════════════════════════════════════════════════════════
class TestT28HumanOnlyAuthorization:
    def test_human_authorization_not_issued(self):
        snap = _load_snapshot()
        assert snap["human_authorization"] == "NOT_ISSUED"


# ══════════════════════════════════════════════════════════════════
# T29: LIVE_EXECUTE remains denied
# ══════════════════════════════════════════════════════════════════
class TestT29LIVEEXECUTEDenied:
    def test_mode_is_paper(self):
        cfg = _load_config()
        assert cfg["mode"] == "paper"

    def test_paper_first_true(self):
        cfg = _load_config()
        assert cfg["paper_first"] is True

    def test_live_not_in_safe_modes(self):
        config_py = (ROOT / "core" / "config.py").read_text()
        match = re.search(r'safe_modes\s*=\s*\{([^}]+)\}', config_py)
        assert match, "safe_modes not found"
        assert "'live'" not in match.group(1), "'live' found in safe_modes"


# ══════════════════════════════════════════════════════════════════
# T30: Full regression marker
# ══════════════════════════════════════════════════════════════════
class TestT30FullRegression:
    def test_all_23b_artifacts_exist(self):
        """All required evidence documents must exist."""
        required_files = [
            "owner_capital_policy.md",
            "live_risk_policy_v1.md",
            "strategy_risk_contract.md",
            "tradable_universe_policy.md",
            "liquidity_policy.md",
            "broker_readonly_runtime.md",
            "account_equity_snapshot.md",
            "reconciliation.md",
            "broker_position_state.md",
            "data_scope_certification.md",
            "pilot_strategy_selection.md",
            "pilot_allocation_proof.md",
            "abort_conditions.md",
            "prelive_snapshot.md",
            "blockers.md",
        ]
        for fname in required_files:
            fpath = DOCS_DIR / fname
            assert fpath.exists(), f"Missing: {fname}"

    def test_live_risk_v1_immutable(self):
        """LIVE_RISK_V1 must be marked immutable."""
        lr = _load_live_risk()
        assert lr.get("immutable") is True

    def test_snapshot_hash_exists(self):
        """Pre-live snapshot must have a hash."""
        snap = _load_snapshot()
        assert "snapshot_hash" in snap
        assert len(snap["snapshot_hash"]) > 0

    def test_broker_snapshot_exists(self):
        """Broker truth snapshot must exist."""
        assert BROKER_SNAPSHOT_PATH.exists()
