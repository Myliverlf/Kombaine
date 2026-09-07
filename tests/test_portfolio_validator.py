"""Tests for portfolio_validator.py — slots, dedup, consistency, RI exclusion.

Fixtures ≥3 (required by task.md acceptance criteria):
  - overloaded_portfolio_fixture: 8 slots with GAZP×4 duplicates (reproducer)
  - open_position_portfolio_fixture: mix of open + closed slots
  - ri_excluded_fixture: portfolio with RI ticker (must be excluded)
  - clean_portfolio_fixture: valid 3-slot portfolio (baseline PASS)
  - single_contract_fixture: all contracts=1 (baseline PASS)

All fixtures are in-memory dicts. No disk I/O to state/.

Источники:
  - plan.md Фича 3
  - analysis.md §2.3 (8 слотов, дубли GAZP)
  - config.json risk.max_slots=3, excluded=["RI"]
"""
import sys
from pathlib import Path

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from portfolio_validator import validate_portfolio, format_report  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def overloaded_portfolio_fixture() -> dict:
    """8 slots with GAZP ft_bband_rsi × 4 duplicates — reproducer of real state.

    Source: state/portfolio.json (8 slots, GAZP×4, LKOH×3, GAZP×1 bollinger)
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
            },
            "slot_LKOH_66001": {
                "ticker": "LKOH",
                "strategy": "nateemma_basket_meanrev",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
            },
            "slot_LKOH_25939": {
                "ticker": "LKOH",
                "strategy": "nfi_trend",
                "contracts": 1,
                "pnl_rub": 0.0,
                "open_position": None,
            },
            "slot_GAZP_25939": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -82.86,
                "open_position": None,
            },
            "slot_GAZP_26886": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -0.1,
                "open_position": None,
            },
            "slot_GAZP_29901": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": -58.86,
                "open_position": None,
            },
            "slot_GAZP_98301": {
                "ticker": "GAZP",
                "strategy": "bollinger_reversion",
                "contracts": 1,
                "pnl_rub": 102.64,
                "open_position": None,
            },
        },
        "peak_equity": 42789.47,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def open_position_portfolio_fixture() -> dict:
    """Portfolio with 3 slots: 1 open, 1 open with bad qty, 1 closed.

    Tests open_position consistency checks.
    """
    return {
        "slots": {
            "slot_A": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 100.0,
                "open_position": {
                    "direction": "SHORT",
                    "qty": 1,
                    "entry_price": 42000.0,
                },
            },
            "slot_B": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 2,  # violates max_contracts_per_entry
                "pnl_rub": -50.0,
                "open_position": {
                    "direction": "LONG",
                    "qty": 1,  # mismatch with contracts=2
                    "entry_price": 80.0,
                },
            },
            "slot_C": {
                "ticker": "SBER",
                "strategy": "mean_reversion",
                "contracts": 1,
                "pnl_rub": 25.0,
                "open_position": None,
            },
        },
        "peak_equity": 20000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def ri_excluded_fixture() -> dict:
    """Portfolio with an RI strategy slot — must be flagged as excluded.

    Source: config.json excluded=["RI"]
    """
    return {
        "slots": {
            "slot_RI_12345": {
                "ticker": "RI",
                "strategy": "nfi_trend",
                "contracts": 1,
                "pnl_rub": 200.0,
                "open_position": None,
            },
            "slot_LKOH_1": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "pnl_rub": 100.0,
                "open_position": None,
            },
        },
        "peak_equity": 21000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def clean_portfolio_fixture() -> dict:
    """Valid 3-slot portfolio — all checks should PASS."""
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
            },
            "slot_GAZP_1": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": 200.0,
                "open_position": None,
            },
            "slot_SBER_1": {
                "ticker": "SBER",
                "strategy": "mean_reversion",
                "contracts": 1,
                "pnl_rub": -50.0,
                "open_position": None,
            },
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


@pytest.fixture
def single_contract_fixture() -> dict:
    """Portfolio where one slot has contracts=3 — should VIOLATE max_contracts_per_entry=1."""
    return {
        "slots": {
            "slot_LKOH_1": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 3,  # VIOLATION: > 1
                "pnl_rub": 500.0,
                "open_position": None,
            },
            "slot_GAZP_1": {
                "ticker": "GAZP",
                "strategy": "ft_bband_rsi",
                "contracts": 1,
                "pnl_rub": 200.0,
                "open_position": None,
            },
        },
        "peak_equity": 100000.0,
        "halted": False,
        "halt_reason": None,
    }


# ── Tests ──────────────────────────────────────────────────────────

class TestSlotCountEnforced:
    """Check 1: slot count <= max_slots."""

    def test_overloaded_portfolio_detected(
        self, overloaded_portfolio_fixture: dict
    ) -> None:
        """8 slots with max_slots=3 must be flagged as VIOLATIONS."""
        result = validate_portfolio(overloaded_portfolio_fixture, max_slots=3)
        assert result["verdict"] == "VIOLATIONS"
        assert result["checks"]["slot_count"]["ok"] is False
        assert result["checks"]["slot_count"]["count"] == 8

    def test_clean_portfolio_passes(
        self, clean_portfolio_fixture: dict
    ) -> None:
        """3 slots with max_slots=3 must PASS slot count check."""
        result = validate_portfolio(clean_portfolio_fixture, max_slots=3)
        assert result["checks"]["slot_count"]["ok"] is True
        assert result["checks"]["slot_count"]["count"] == 3

    def test_empty_portfolio_passes(self) -> None:
        """Empty portfolio passes slot count check."""
        result = validate_portfolio({"slots": {}}, max_slots=3)
        assert result["checks"]["slot_count"]["ok"] is True
        assert result["checks"]["slot_count"]["count"] == 0


class TestNoDuplicates:
    """Check 2: no duplicate (ticker, strategy) pairs."""

    def test_duplicates_detected(
        self, overloaded_portfolio_fixture: dict
    ) -> None:
        """GAZP ft_bband_rsi ×4 must be detected as duplicates."""
        result = validate_portfolio(overloaded_portfolio_fixture)
        assert result["checks"]["duplicates"]["ok"] is False
        dupes = result["checks"]["duplicates"]["found"]
        # Find GAZP ft_bband_rsi among duplicates
        gazp_dupes = [d for d in dupes if d["ticker"] == "GAZP" and d["strategy"] == "ft_bband_rsi"]
        assert len(gazp_dupes) == 1
        assert len(gazp_dupes[0]["slot_ids"]) == 4  # 4 duplicate slots

    def test_no_duplicates_clean(
        self, clean_portfolio_fixture: dict
    ) -> None:
        """Clean portfolio with unique (ticker, strategy) pairs passes."""
        result = validate_portfolio(clean_portfolio_fixture)
        assert result["checks"]["duplicates"]["ok"] is True


class TestOpenPositionConsistency:
    """Check 3: open_position consistency — missing fields, qty mismatch."""

    def test_open_position_qty_mismatch(
        self, open_position_portfolio_fixture: dict
    ) -> None:
        """Slot B has contracts=2 but open_position.qty=1 — should detect mismatch."""
        result = validate_portfolio(
            open_position_portfolio_fixture,
            max_contracts_per_entry=3,  # allow contracts=2 for this test
        )
        assert result["checks"]["open_position_consistency"]["ok"] is False
        issues = result["checks"]["open_position_consistency"]["issues"]
        assert any("slot_B" in issue and "qty" in issue for issue in issues)

    def test_valid_open_position_passes(
        self, clean_portfolio_fixture: dict
    ) -> None:
        """Valid open_position with matching qty and contracts passes."""
        result = validate_portfolio(clean_portfolio_fixture)
        assert result["checks"]["open_position_consistency"]["ok"] is True


class TestContractsPerEntry:
    """Check 4: contracts per entry <= max_contracts_per_entry."""

    def test_multi_contract_violation(
        self, single_contract_fixture: dict
    ) -> None:
        """Slot with contracts=3 must violate max_contracts_per_entry=1."""
        result = validate_portfolio(single_contract_fixture, max_contracts_per_entry=1)
        assert result["checks"]["contracts_per_entry"]["ok"] is False
        assert result["checks"]["contracts_per_entry"]["max_found"] == 3
        assert any("contracts=3" in v for v in result["violations"])

    def test_all_contracts_ok(
        self, clean_portfolio_fixture: dict
    ) -> None:
        """All slots with contracts=1 pass."""
        result = validate_portfolio(clean_portfolio_fixture, max_contracts_per_entry=1)
        assert result["checks"]["contracts_per_entry"]["ok"] is True
        assert result["checks"]["contracts_per_entry"]["max_found"] == 1


class TestRIExcluded:
    """Check 5: RI ticker must be excluded from portfolio."""

    def test_ri_in_portfolio_detected(
        self, ri_excluded_fixture: dict
    ) -> None:
        """RI slot must be flagged as excluded."""
        result = validate_portfolio(ri_excluded_fixture)
        assert result["checks"]["excluded_tickers"]["ok"] is False
        found = result["checks"]["excluded_tickers"]["found"]
        assert any("RI" in f for f in found)

    def test_ri_absent_passes(
        self, clean_portfolio_fixture: dict
    ) -> None:
        """Portfolio without RI passes exclusion check."""
        result = validate_portfolio(clean_portfolio_fixture)
        assert result["checks"]["excluded_tickers"]["ok"] is True


class TestScorecard:
    """Composite PnL/risk scorecard computation."""

    def test_scorecard_computed(
        self, overloaded_portfolio_fixture: dict
    ) -> None:
        """Scorecard must be computed with correct totals."""
        result = validate_portfolio(overloaded_portfolio_fixture)
        sc = result["scorecard"]
        assert sc["total_slots"] == 8
        assert sc["total_open_positions"] == 2
        assert sc["total_pnl_rub"] != 0.0  # sum of non-zero PnLs
        assert sc["unique_tickers"] >= 2  # GAZP + LKOH
        assert sc["slots_with_pnl"] > 0

    def test_scorecard_empty_portfolio(self) -> None:
        """Empty portfolio yields zero scorecard."""
        result = validate_portfolio({"slots": {}})
        sc = result["scorecard"]
        assert sc["total_slots"] == 0
        assert sc["total_pnl_rub"] == 0.0


class TestFormatReport:
    """format_report utility."""

    def test_format_report_contains_verdict(
        self, clean_portfolio_fixture: dict
    ) -> None:
        """Report string contains PASS/VIOLATIONS and key metrics."""
        result = validate_portfolio(clean_portfolio_fixture)
        report = format_report(result)
        assert "PASS" in report or "VIOLATIONS" in report
        assert "PnL" in report
        assert "Slots:" in report
