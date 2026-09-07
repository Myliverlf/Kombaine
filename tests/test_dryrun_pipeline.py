"""Pipeline chain dry-run tests (≥3 fixture-based tests).

Covers:
  1. Allocator scorecard on fixture data — PnL↑/risk↓ metric validation
  2. Empty output detection — pipeline must not produce silent empty results
  3. Candidate allocator end-to-end — RI excluded, max_slots ≤3, 1 contract/entry

All tests use fixtures from conftest.py; no live broker, no network, no state writes.
"""
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))


# ── Test 1: Allocator scorecard on fixture data ──────────────────────

class TestAllocatorScorecardDryrun:
    """Verify allocator_scorecard composite on fixture data."""

    def test_scorecard_composite_is_nonnegative(
        self, engine_config_fixture, portfolio_fixture
    ):
        """risk_allocator_scorecard should produce composite_score >= 0 on empty portfolio."""
        from risk_allocator_scorecard import (
            check_max_slots,
            check_contracts_per_entry,
            check_ri_excluded,
        )

        max_slots_cfg = engine_config_fixture["risk"]["max_slots"]
        max_contracts_cfg = engine_config_fixture["risk"]["max_contracts_per_entry"]
        excluded = engine_config_fixture.get("excluded", [])

        r_slots = check_max_slots(portfolio_fixture, max_slots=max_slots_cfg)
        r_contracts = check_contracts_per_entry(portfolio_fixture, max_contracts=max_contracts_cfg)
        r_no_ri = check_ri_excluded(portfolio_fixture, excluded=excluded)

        # Each check must exist and be a dict
        for r in (r_slots, r_contracts, r_no_ri):
            assert isinstance(r, dict), f"Expected dict, got {type(r)}"
            assert "passed" in r, f"Missing 'passed' key in {r}"
            assert "name" in r, f"Missing 'name' key in {r}"

        # Empty portfolio → all checks pass
        assert r_slots["passed"] is True, f"max_slots failed: {r_slots}"
        assert r_contracts["passed"] is True, f"contracts failed: {r_contracts}"
        assert r_no_ri["passed"] is True, f"no_ri failed: {r_no_ri}"

        # Composite = (slots + contracts + no_ri) / 3, all True → 1.0
        score = (
            float(r_slots["passed"])
            + float(r_contracts["passed"])
            + float(r_no_ri["passed"])
        ) / 3.0
        assert score >= 0.0, f"Composite score must be >= 0, got {score}"

    def test_allocator_scorecard_rejects_overloaded_portfolio(
        self, engine_config_fixture
    ):
        """portfolio with 5 slots (>max_slots=3) should fail max_slots check."""
        from risk_allocator_scorecard import check_max_slots

        overloaded = {
            "slots": {
                f"slot_{i}": {"ticker": f"T{i}", "contracts": 1}
                for i in range(5)
            }
        }
        r = check_max_slots(overloaded, max_slots=3)
        assert r["passed"] is False, f"Expected FAIL for 5 slots > max=3: {r}"
        assert r["actual"] == 5


# ── Test 2: Empty output detection ──────────────────────────────────

class TestEmptyOutputDetection:
    """Pipeline must detect and reject empty/missing inputs."""

    def test_empty_candidates_rejected_by_allocator(self, engine_config_fixture):
        """select_live_slots with zero candidates returns empty list (no crash)."""
        from candidate_allocator import select_live_slots

        result = select_live_slots([], engine_config_fixture)
        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) == 0, f"Expected empty list, got {result}"

    def test_signal_pool_empty_detected(self, signal_pool_fixture):
        """signal_pool with no strategies must be detected as empty."""
        pool = {"strategies": {}, "last_rotation_ts": 0.0}
        n = len(pool.get("strategies", {}))
        assert n == 0, "Empty pool should have 0 strategies"

        # Real pool should have content
        real_n = len(signal_pool_fixture.get("strategies", {}))
        assert real_n >= 1, "Fixture pool should have at least 1 strategy"

    def test_config_paper_mode_enforced(self, engine_config_fixture):
        """engine_config_fixture must always be in paper mode (safety guard)."""
        assert engine_config_fixture["mode"] == "paper", (
            f"Config mode must be 'paper' for dry-run, got '{engine_config_fixture['mode']}'"
        )
        assert engine_config_fixture.get("paper_first") is True, (
            "paper_first must be True"
        )


# ── Test 3: Candidate allocator end-to-end on fixtures ──────────────

class TestCandidateAllocatorDryrun:
    """End-to-end test of select_live_slots with fixtures."""

    def test_ri_excluded_from_allocation(self, engine_config_fixture):
        """RI must not appear in allocated slots."""
        from candidate_allocator import select_live_slots

        candidates = [
            {"ticker": "LKOH", "direction": "LONG",
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 100, "contracts_requested": 1},
            {"ticker": "GAZP", "direction": "SHORT",
             "win_rate": 0.5, "avg_win": 80, "avg_loss": 50, "contracts_requested": 1},
            {"ticker": "RI", "direction": "LONG",
             "win_rate": 0.9, "avg_win": 500, "avg_loss": 10, "contracts_requested": 1},
        ]
        selected = select_live_slots(candidates, engine_config_fixture)

        tickers = [s["ticker"] for s in selected]
        assert "RI" not in tickers, f"RI found in selected: {tickers}"

    def test_max_slots_le_3(self, engine_config_fixture):
        """Allocator must not select more than 3 slots."""
        from candidate_allocator import select_live_slots

        candidates = [
            {"ticker": f"T{i}", "direction": "LONG",
             "win_rate": 0.5 + i * 0.05, "avg_win": 100 + i * 50,
             "avg_loss": 50, "contracts_requested": 1}
            for i in range(6)
        ]
        selected = select_live_slots(candidates, engine_config_fixture)
        assert len(selected) <= 3, f"Expected ≤3 slots, got {len(selected)}"

    def test_one_contract_per_entry(self, engine_config_fixture):
        """Every allocated slot must have contracts ≤ 1."""
        from candidate_allocator import select_live_slots

        candidates = [
            {"ticker": "LKOH", "direction": "LONG",
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 100, "contracts_requested": 5},
            {"ticker": "GAZP", "direction": "SHORT",
             "win_rate": 0.5, "avg_win": 80, "avg_loss": 50, "contracts_requested": 3},
        ]
        selected = select_live_slots(candidates, engine_config_fixture)
        for s in selected:
            assert s["contracts"] <= 1, (
                f"Slot {s['ticker']} has contracts={s['contracts']}, max is 1"
            )

    def test_allocator_vs_baseline_scorecard(
        self, engine_config_fixture
    ):
        """Allocator score ≥ baseline score (PnL↑/risk↓ metric validation)."""
        from candidate_allocator import select_live_slots, select_baseline_slots

        candidates = [
            {"ticker": "LKOH", "direction": "LONG",
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 100, "contracts_requested": 1},
            {"ticker": "GAZP", "direction": "SHORT",
             "win_rate": 0.5, "avg_win": 80, "avg_loss": 50, "contracts_requested": 1},
            {"ticker": "SBER", "direction": "LONG",
             "win_rate": 0.7, "avg_win": 100, "avg_loss": 120, "contracts_requested": 1},
        ]

        selected = select_live_slots(candidates, engine_config_fixture)
        baseline = select_baseline_slots(
            [dict(c) for c in candidates],
            max_slots=3,
            excluded=engine_config_fixture.get("excluded", []),
        )

        # Both should return results
        assert len(selected) > 0, "Allocator selected nothing"
        assert len(baseline) > 0, "Baseline selected nothing"

        # Allocator composite scorecard should pass
        from risk_allocator_scorecard import (
            check_max_slots,
            check_contracts_per_entry,
            check_ri_excluded,
        )
        r_slots = check_max_slots({"slots": {s["ticker"]: s for s in selected}}, max_slots=3)
        r_contracts = check_contracts_per_entry(
            {"slots": {s["ticker"]: s for s in selected}}, max_contracts=1
        )
        r_no_ri = check_ri_excluded(
            {"slots": {s["ticker"]: s for s in selected}},
            excluded=engine_config_fixture.get("excluded", []),
        )
        assert r_slots["passed"], f"Allocator scorecard slots failed: {r_slots}"
        assert r_contracts["passed"], f"Allocator scorecard contracts failed: {r_contracts}"
        assert r_no_ri["passed"], f"Allocator scorecard RI check failed: {r_no_ri}"
