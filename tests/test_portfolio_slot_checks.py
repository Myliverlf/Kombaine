"""Tests for portfolio slot checks: count, duplicates, dead slots, dedup, risk crosscheck.

Features covered (from plan.md):
  F1: Slot count ≤ max + duplicate detection + dead slot finder
  F2: Dedup best-slot selection (enforce on tmp_path fixture, no real state/)
  F4: Risk/scorecard crosscheck + RI exclusion + contracts cap

Fixtures (5):
  - overfilled_portfolio: 8 slots with GAZP ft_bband_rsi × 4 duplicates
  - valid_2slot_portfolio: 2 unique (ticker, strategy) pairs, ≤ max_slots
  - dead_slot_portfolio: slots with n_trades=0, pnl=0, open_position=null
  - portfolio_with_open_and_dupes: duplicates where one has open_position
  - clean_3slot_portfolio: valid 3-slot portfolio for scorecard baseline

All fixtures are in-memory dicts. No disk I/O to real state/.
Enforce tests write to tmp_path only.

Источники:
  - plan.md Фичи 1, 2, 4
  - analysis.md §2.3 (8 слотов, дубли GAZP×4)
  - code/portfolio_validator.py
  - code/portfolio_enforcer.py
  - code/risk_allocator_scorecard.py
"""
import json
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
sys.path.insert(0, str(CODE_DIR))
sys.path.insert(0, str(CORE_DIR))

from portfolio_validator import validate_portfolio  # noqa: E402
from portfolio_enforcer import enforce_portfolio, _slot_sort_key  # noqa: E402
from risk_allocator_scorecard import (  # noqa: E402
    check_max_slots,
    check_contracts_per_entry,
    check_ri_excluded,
    compute_composite_score,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def overfilled_portfolio() -> dict:
    """8 slots with GAZP ft_bband_rsi × 4 duplicates — reproducer of real state.

    2 open positions (LKOH vwap_reversion, GAZP ft_bband_rsi).
    Source: state/portfolio.json (8 slots, GAZP×4, LKOH×2, GAZP×1 bollinger)
    """
    return {
        "slots": {
            "slot_LKOH_40085": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 538.21,
                "open_position": {
                    "direction": "SHORT",
                    "qty": 1,
                    "entry_price": 42598.0,
                },
                "promoted_ts": 1000.0,
                "n_trades": 5,
            },
            "slot_GAZP_40085": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -30.08,
                "open_position": {
                    "direction": "SHORT",
                    "qty": 1,
                    "entry_price": 83.94,
                },
                "promoted_ts": 2000.0,
                "n_trades": 3,
            },
            "slot_GAZP_25939": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -82.86,
                "open_position": None,
                "promoted_ts": 3000.0,
                "n_trades": 2,
            },
            "slot_GAZP_26886": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -0.1,
                "open_position": None,
                "promoted_ts": 4000.0,
                "n_trades": 1,
            },
            "slot_GAZP_29901": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -58.86,
                "open_position": None,
                "promoted_ts": 5000.0,
                "n_trades": 1,
            },
            "slot_LKOH_66001": {
                "ticker": "LKOH",
                "strategy": "nateemma_basket_meanrev",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
                "promoted_ts": 6000.0,
                "n_trades": 0,
            },
            "slot_LKOH_25939": {
                "ticker": "LKOH",
                "strategy": "nfi_trend",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
                "promoted_ts": 7000.0,
                "n_trades": 0,
            },
            "slot_GAZP_98301": {
                "ticker": "GAZP",
                "strategy": "bollinger_reversion",
                "contracts": 1,
                "pnl_rub": 102.64,
                "open_position": None,
                "promoted_ts": 8000.0,
                "n_trades": 4,
            },
        },
        "peak_equity": 42789.47,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def valid_2slot_portfolio() -> dict:
    """2 unique (ticker, strategy) pairs — valid for max_slots=3."""
    return {
        "slots": {
            "slot_LKOH_1": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 250.0,
                "open_position": None,
                "promoted_ts": 1000.0,
                "n_trades": 10,
            },
            "slot_GAZP_1": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -30.0,
                "open_position": None,
                "promoted_ts": 2000.0,
                "n_trades": 5,
            },
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def dead_slot_portfolio() -> dict:
    """Portfolio with dead slots: n_trades=0, pnl=0, open_position=null.

    Dead slots are candidates for removal but not automatically removed.
    """
    return {
        "slots": {
            "slot_alive_1": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 500.0,
                "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 42000.0},
                "promoted_ts": 1000.0,
                "n_trades": 15,
            },
            "slot_dead_1": {
                "ticker": "GAZP",
                "strategy": "nfi_trend",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
                "promoted_ts": 2000.0,
                "n_trades": 0,
            },
            "slot_dead_2": {
                "ticker": "SBER",
                "strategy": "mean_reversion",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
                "promoted_ts": 3000.0,
                "n_trades": 0,
            },
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def portfolio_with_open_and_dupes() -> dict:
    """Duplicates where one slot has open_position — dedup must keep it.

    3 slots: GAZP ft_bband_rsi × 2 (one with open_position), LKOH unique.
    After enforce to max_slots=3: 2 unique (ticker, strategy), both kept.
    """
    return {
        "slots": {
            "slot_GAZP_open": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -10.0,  # worse PnL
                "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 83.0},
                "promoted_ts": 1000.0,
                "n_trades": 3,
            },
            "slot_GAZP_better": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": 200.0,  # better PnL, but no open_position
                "open_position": None,
                "promoted_ts": 2000.0,
                "n_trades": 8,
            },
            "slot_LKOH_1": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 100.0,
                "open_position": None,
                "promoted_ts": 3000.0,
                "n_trades": 5,
            },
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def clean_3slot_portfolio() -> dict:
    """Valid 3-slot portfolio — all checks PASS."""
    return {
        "slots": {
            "slot_LKOH_1": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 500.0,
                "open_position": {
                    "direction": "SHORT",
                    "qty": 1,
                    "entry_price": 42000.0,
                },
                "promoted_ts": 1000.0,
                "n_trades": 20,
            },
            "slot_GAZP_1": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": 200.0,
                "open_position": None,
                "promoted_ts": 2000.0,
                "n_trades": 10,
            },
            "slot_SBER_1": {
                "ticker": "SBER",
                "strategy": "mean_reversion",
                "contracts": 1,
                "pnl_rub": -50.0,
                "open_position": None,
                "promoted_ts": 3000.0,
                "n_trades": 7,
            },
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


# ── Helper ─────────────────────────────────────────────────────────


def _find_dead_slots(portfolio: dict) -> list[str]:
    """Find dead slots: n_trades=0, pnl=0, open_position=null.

    Pure function — no side effects.
    """
    dead = []
    for slot_id, slot in portfolio.get("slots", {}).items():
        if (
            slot.get("n_trades", 0) == 0
            and slot.get("pnl_rub", 0.0) == 0.0
            and slot.get("open_position") is None
        ):
            dead.append(slot_id)
    return dead


# ── Feature 1: Slot count ≤ max + duplicate detection + dead slots ─


class TestSlotCountExceedsMax:
    """Validator detects slot count > max_slots."""

    def test_slot_count_exceeds_max(self, overfilled_portfolio: dict) -> None:
        """8 slots with max_slots=3 must be flagged as VIOLATIONS."""
        result = validate_portfolio(overfilled_portfolio, max_slots=3)
        assert result["verdict"] == "VIOLATIONS"
        sc = result["checks"]["slot_count"]
        assert sc["ok"] is False
        assert sc["count"] == 8
        assert sc["max"] == 3

    def test_valid_slot_count_passes(self, valid_2slot_portfolio: dict) -> None:
        """2 slots with max_slots=3 passes."""
        result = validate_portfolio(valid_2slot_portfolio, max_slots=3)
        assert result["checks"]["slot_count"]["ok"] is True
        assert result["checks"]["slot_count"]["count"] == 2


class TestDuplicateDetection:
    """Validator detects duplicate (ticker, strategy) pairs."""

    def test_duplicate_detection(self, overfilled_portfolio: dict) -> None:
        """4× GAZP ft_bband_rsi detected as duplicates."""
        result = validate_portfolio(overfilled_portfolio)
        assert result["checks"]["duplicates"]["ok"] is False
        dupes = result["checks"]["duplicates"]["found"]
        gazp_dupes = [
            d for d in dupes
            if d["ticker"] == "GAZP" and d["strategy"] == "ft_bband_rsi"
        ]
        assert len(gazp_dupes) == 1
        assert len(gazp_dupes[0]["slot_ids"]) == 4

    def test_no_duplicates_valid(self, valid_2slot_portfolio: dict) -> None:
        """Unique (ticker, strategy) pairs — no duplicates."""
        result = validate_portfolio(valid_2slot_portfolio)
        assert result["checks"]["duplicates"]["ok"] is True


class TestDeadSlotDetected:
    """Dead slots: n_trades=0, pnl=0, open_position=null."""

    def test_dead_slot_detected(self, dead_slot_portfolio: dict) -> None:
        """Dead slots (n_trades=0, pnl=0, no position) are identified."""
        dead = _find_dead_slots(dead_slot_portfolio)
        assert len(dead) == 2
        assert "slot_dead_1" in dead
        assert "slot_dead_2" in dead

    def test_alive_slot_not_dead(self, dead_slot_portfolio: dict) -> None:
        """Slot with n_trades>0 and pnl!=0 is NOT dead."""
        dead = _find_dead_slots(dead_slot_portfolio)
        assert "slot_alive_1" not in dead

    def test_dead_slot_with_open_position_not_dead(self) -> None:
        """Slot with n_trades=0 but open_position is NOT dead (has active trade)."""
        portfolio = {
            "slots": {
                "slot_X": {
                    "ticker": "GAZP",
                    "strategy": "test",
                    "contracts": 1,
                    "pnl_rub": 0.0,
                    "n_trades": 0,
                    "open_position": {"direction": "LONG", "qty": 1, "entry_price": 80.0},
                },
            }
        }
        dead = _find_dead_slots(portfolio)
        assert len(dead) == 0


# ── Feature 2: Dedup best-slot selection (enforce on tmp_path) ─────


class TestDedupBestSlot:
    """Enforce deduplicates slots — keeps best by sort key."""

    def test_dedup_keeps_best_slot(
        self, overfilled_portfolio: dict, tmp_path: Path
    ) -> None:
        """After enforce: 4 GAZP ft_bband_rsi → 1 with best pnl_rub."""
        portfolio_file = tmp_path / "portfolio.json"
        portfolio_file.write_text(json.dumps(overfilled_portfolio, indent=2))

        before, after, removed = enforce_portfolio(portfolio_file, max_slots=3)

        assert before == 8
        assert after == 3
        assert len(removed) == 5

        # Verify result on disk
        result_portfolio = json.loads(portfolio_file.read_text())
        result_slots = result_portfolio["slots"]

        # Only 3 slots remain
        assert len(result_slots) == 3

        # Check GAZP ft_bband_rsi is represented exactly once
        gazp_bband = [
            s for s in result_slots.values()
            if s.get("ticker") == "GAZP" and s.get("strategy") == "ft_bband_rsi"
        ]
        assert len(gazp_bband) == 1

        # The best one by _slot_sort_key should be kept:
        # slot_GAZP_40085 has open_position → highest priority
        kept_ids = set(result_slots.keys())
        assert "slot_GAZP_40085" in kept_ids

    def test_dedup_preserves_open_position(
        self, portfolio_with_open_and_dupes: dict, tmp_path: Path
    ) -> None:
        """Slot with open_position survives dedup even if pnl is lower.

        _slot_sort_key: (-has_position, -pnl, promoted_ts)
        Lower tuple = higher priority. open_position => (-1, ...) < (-0, ...)
        So open_position slot always wins dedup.
        """
        portfolio_file = tmp_path / "portfolio.json"
        portfolio_file.write_text(json.dumps(portfolio_with_open_and_dupes, indent=2))

        enforce_portfolio(portfolio_file, max_slots=3)

        result = json.loads(portfolio_file.read_text())
        slots = result["slots"]

        # GAZP ft_bband_rsi: the one with open_position must survive
        gazp_slots = [
            s for s in slots.values()
            if s.get("ticker") == "GAZP" and s.get("strategy") == "ft_bband_rsi"
        ]
        assert len(gazp_slots) == 1
        assert gazp_slots[0]["open_position"] is not None
        assert gazp_slots[0]["pnl_rub"] == -10.0  # the worse PnL but has position

    def test_dedup_no_change_when_no_duplicates(
        self, valid_2slot_portfolio: dict, tmp_path: Path
    ) -> None:
        """No duplicates + count ≤ max_slots → enforce is a no-op."""
        portfolio_file = tmp_path / "portfolio.json"
        portfolio_file.write_text(json.dumps(valid_2slot_portfolio, indent=2))

        before, after, removed = enforce_portfolio(portfolio_file, max_slots=3)

        assert before == after == 2
        assert removed == []

        # File unchanged
        result = json.loads(portfolio_file.read_text())
        assert len(result["slots"]) == 2


# ── Feature 4: Risk/scorecard crosscheck + RI exclusion + contracts ─


class TestRiskScorecardComposite:
    """Risk/allocator scorecard returns composite checks."""

    def test_risk_scorecard_has_composite(self, clean_3slot_portfolio: dict) -> None:
        """Scorecard checks return ≥3 items, all ok=True for clean portfolio."""
        checks = [
            check_max_slots(clean_3slot_portfolio, max_slots=3),
            check_contracts_per_entry(clean_3slot_portfolio, max_contracts=1),
            check_ri_excluded(clean_3slot_portfolio, excluded=["RI"]),
        ]
        assert len(checks) >= 3
        for c in checks:
            assert c["passed"] is True, f"check {c['name']} failed"

        composite = compute_composite_score(checks)
        assert composite == 1.0

    def test_scorecard_detects_violations(self, overfilled_portfolio: dict) -> None:
        """Overloaded portfolio yields composite < 1.0."""
        checks = [
            check_max_slots(overfilled_portfolio, max_slots=3),
            check_contracts_per_entry(overfilled_portfolio, max_contracts=1),
            check_ri_excluded(overfilled_portfolio, excluded=["RI"]),
        ]
        # max_slots check should fail
        assert checks[0]["passed"] is False
        composite = compute_composite_score(checks)
        assert composite < 1.0


class TestRIExcluded:
    """RI ticker must not appear in candidates."""

    def test_ri_excluded(self, clean_3slot_portfolio: dict) -> None:
        """No RI slots in clean portfolio."""
        result = check_ri_excluded(clean_3slot_portfolio, excluded=["RI"])
        assert result["passed"] is True
        assert len(result["ri_slots"]) == 0

    def test_ri_detected_when_present(self) -> None:
        """RI slot is detected in portfolio."""
        portfolio = {
            "slots": {
                "slot_RI_1": {
                    "ticker": "RI",
                    "strategy": "nfi_trend",
                    "contracts": 1,
                    "pnl_rub": 100.0,
                },
            }
        }
        result = check_ri_excluded(portfolio, excluded=["RI"])
        assert result["passed"] is False
        assert len(result["ri_slots"]) == 1


class TestMaxContractsPerEntry:
    """All slots must have contracts ≤ max_contracts_per_entry."""

    def test_max_contracts_per_entry(self, overfilled_portfolio: dict) -> None:
        """All slots in overfilled portfolio have contracts=1 → passes."""
        result = check_contracts_per_entry(overfilled_portfolio, max_contracts=1)
        assert result["passed"] is True
        assert result["violations"] == []

    def test_multi_contract_detected(self) -> None:
        """Slot with contracts=2 violates max_contracts=1."""
        portfolio = {
            "slots": {
                "slot_A": {
                    "ticker": "GAZP",
                    "strategy": "test",
                    "contracts": 2,
                    "pnl_rub": 0.0,
                },
            }
        }
        result = check_contracts_per_entry(portfolio, max_contracts=1)
        assert result["passed"] is False
        assert len(result["violations"]) == 1
