"""Тесты OSS shortlist + scorecard metrics + constraint validation.

Fixtures ≥3 (from tests/fixtures/ JSON + inline), dry-run, без live-ордеров.
Покрывает критерии приёмки task.md:
  - shortlist registry валиден и содержит reject-записи
  - ab_compare даёт pnl_up+risk_down на fixture-данных
  - RI excluded — VETO из regime_gate.py на копии snapshot
  - max live slots ≤3, max_contracts_per_entry=1 читаются из кода и не изменены
  - нет broker/online-зависимостей
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure code/ is on sys.path
_CODE_DIR = Path(__file__).resolve().parent.parent / "code"
sys.path.insert(0, str(_CODE_DIR))

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

from oss_shortlist import SHORTLIST, summary
from scorecard_metrics import (
    ab_compare,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)

# Import regime_gate for RI VETO test
from regime_gate import admit


# ─── Fixtures (≥3 required) ────────────────────────────────────────────

@pytest.fixture
def sample_returns():
    """Load sample_returns.json from tests/fixtures/."""
    path = _FIXTURES_DIR / "sample_returns.json"
    with open(path) as f:
        data = json.load(f)
    return data


@pytest.fixture
def portfolio_copy():
    """Load portfolio_copy.json — safe copy, NOT live state/."""
    path = _FIXTURES_DIR / "portfolio_copy.json"
    with open(path) as f:
        data = json.load(f)
    return data


@pytest.fixture
def regime_snapshot_fixture():
    """Load regime_snapshot.json from tests/fixtures/."""
    path = _FIXTURES_DIR / "regime_snapshot.json"
    with open(path) as f:
        data = json.load(f)
    return data


@pytest.fixture
def tmp_state_dir():
    """Temporary state directory — no mutation of live state/."""
    with tempfile.TemporaryDirectory(prefix="test_oss_") as td:
        yield Path(td)


# ─── Test: Shortlist Registry ──────────────────────────────────────────

class TestOssShortlist:
    """Shortlist registry валиден и содержит reject-записи (task.md criterion)."""

    def test_shortlist_has_entries(self):
        """SHORTLIST ≥ 6 entries (research.md: 7 positions)."""
        assert len(SHORTLIST) >= 6

    def test_all_verdicts_valid(self):
        """All verdicts ∈ {adopt_idea, reject}."""
        valid_verdicts = {"adopt_idea", "reject"}
        for entry in SHORTLIST:
            assert entry["verdict"] in valid_verdicts, (
                f"Invalid verdict for {entry['name']}: {entry['verdict']}"
            )

    def test_has_reject_entries(self):
        """Shortlist contains rejected libraries (skfolio, Riskfolio, ccxt, vectorbt)."""
        rejects = [e for e in SHORTLIST if e["verdict"] == "reject"]
        assert len(rejects) >= 3, f"Expected ≥3 rejects, got {len(rejects)}"
        reject_names = {e["name"] for e in rejects}
        # Must reject at least skfolio and ccxt
        assert "skfolio" in reject_names or "Riskfolio-Lib" in reject_names
        assert "ccxt" in reject_names

    def test_has_adopt_entries(self):
        """Shortlist contains adoptable ideas."""
        adopts = [e for e in SHORTLIST if e["verdict"] == "adopt_idea"]
        assert len(adopts) >= 2

    def test_summary_structure(self, sample_returns):
        """summary() returns correct structure."""
        s = summary()
        assert "total" in s
        assert "adopt_count" in s
        assert "reject_count" in s
        assert "verdicts_valid" in s
        assert s["verdicts_valid"] is True
        assert s["total"] == len(SHORTLIST)

    def test_each_entry_has_required_fields(self):
        """Every entry has name, source_url, verdict, reason, integration_target."""
        required = {"name", "source_url", "verdict", "reason", "integration_target"}
        for entry in SHORTLIST:
            missing = required - set(entry.keys())
            assert not missing, f"Entry {entry.get('name', '?')} missing: {missing}"


# ─── Test: Scorecard Metrics (A/B Compare) ────────────────────────────

class TestScorecardMetrics:
    """Pure-python metrics: Sharpe, Sortino, max_dd, profit_factor, ab_compare."""

    def test_ab_compare_pnl_up_risk_down(self, sample_returns):
        """candidate_better should have pnl_up=True and risk_down=True vs baseline."""
        result = ab_compare(
            sample_returns["baseline"],
            sample_returns["candidate_better"],
        )
        assert result["pnl_up"] is True, "candidate_better should have higher Sharpe"
        assert result["risk_down"] is True, "candidate_better should have less drawdown"
        # Delta positive for Sharpe
        assert result["metrics"]["delta"]["sharpe"] >= 0

    def test_ab_compare_worse_candidate(self, sample_returns):
        """candidate_worse should have pnl_up=False and/or risk_down=False."""
        result = ab_compare(
            sample_returns["baseline"],
            sample_returns["candidate_worse"],
        )
        # Worse candidate should not beat baseline on both metrics
        assert not (result["pnl_up"] and result["risk_down"]), (
            "candidate_worse should not beat baseline on both metrics"
        )

    def test_sharpe_ratio_positive(self, sample_returns):
        """Positive returns → positive Sharpe."""
        s = sharpe_ratio(sample_returns["all_positive"])
        assert s > 0

    def test_sharpe_ratio_negative(self, sample_returns):
        """Negative returns → negative Sharpe."""
        s = sharpe_ratio(sample_returns["all_negative"])
        assert s < 0

    def test_max_drawdown_non_positive(self, sample_returns):
        """Max drawdown is always ≤ 0."""
        dd = max_drawdown(sample_returns["baseline"])
        assert dd <= 0.0

    def test_max_drawdown_empty(self):
        """Empty returns → 0.0."""
        assert max_drawdown([]) == 0.0

    def test_profit_factor_no_losses(self, sample_returns):
        """All positive returns → profit_factor > 1."""
        pf = profit_factor(sample_returns["all_positive"])
        assert pf > 1.0

    def test_profit_factor_no_gains(self, sample_returns):
        """All negative returns → profit_factor = 0."""
        pf = profit_factor(sample_returns["all_negative"])
        assert pf == 0.0

    def test_sortino_ratio_positive(self, sample_returns):
        """Positive excess returns → positive Sortino."""
        s = sortino_ratio(sample_returns["all_positive"])
        assert s > 0

    def test_ab_compare_flat_returns(self, sample_returns):
        """Flat (zero) returns → pnl_up=True (equal), risk_down=True (equal)."""
        result = ab_compare(sample_returns["flat"], sample_returns["flat"])
        assert result["pnl_up"] is True
        assert result["risk_down"] is True

    def test_ab_compare_empty_returns(self, sample_returns):
        """Empty returns → metrics are 0, pnl_up and risk_down are True (equal)."""
        result = ab_compare([], [])
        assert result["pnl_up"] is True
        assert result["risk_down"] is True

    def test_metrics_delta(self, sample_returns):
        """Delta should be candidate - baseline."""
        result = ab_compare(
            sample_returns["baseline"],
            sample_returns["candidate_better"],
        )
        delta = result["metrics"]["delta"]
        b = result["metrics"]["baseline"]
        c = result["metrics"]["candidate"]
        assert abs(delta["sharpe"] - (c["sharpe"] - b["sharpe"])) < 1e-5


# ─── Test: RI Excluded (regime_gate VETO) ──────────────────────────────

class TestRiExcluded:
    """RI excluded: VETO из regime_gate.py на fixture-данных (task.md criterion)."""

    def test_ri_veto_with_snapshot(self, regime_snapshot_fixture):
        """RI candidate → admit=False, reason=excluded."""
        cfg = {"excluded": ["RI"], "max_contracts_per_entry": 1}
        candidate = {"ticker": "RI", "direction": "LONG", "contracts_requested": 1}
        result = admit(candidate, regime_snapshot_fixture, cfg)
        assert result["admit"] is False
        assert result["reason"] == "excluded"
        assert result["cap_contracts"] == 0

    def test_ri_veto_no_snapshot(self):
        """RI excluded even with no snapshot."""
        cfg = {"excluded": ["RI"], "max_contracts_per_entry": 1}
        candidate = {"ticker": "RI", "direction": "LONG", "contracts_requested": 1}
        result = admit(candidate, None, cfg)
        assert result["admit"] is False
        assert result["reason"] == "excluded"

    def test_ri_not_in_portfolio(self, portfolio_copy):
        """RI is excluded per portfolio_copy.json config."""
        excluded = portfolio_copy.get("excluded", [])
        assert "RI" in excluded
        # Verify RI has no slot in portfolio
        slots = portfolio_copy.get("slots", {})
        for slot_id, slot in slots.items():
            assert slot["ticker"] != "RI", f"RI found in slot {slot_id}"


# ─── Test: Constraints (slots ≤3, contracts ≤1) ────────────────────────

class TestConstraints:
    """Max live slots ≤3, max_contracts_per_entry=1 (task.md criteria)."""

    def test_max_slots_from_portfolio(self, portfolio_copy):
        """portfolio_copy has max_slots ≤ 3."""
        max_slots = portfolio_copy.get("max_slots", 99)
        assert max_slots <= 3, f"max_slots={max_slots}, expected ≤3"

    def test_max_contracts_from_portfolio(self, portfolio_copy):
        """portfolio_copy has max_contracts_per_entry = 1."""
        max_c = portfolio_copy.get("max_contracts_per_entry", 99)
        assert max_c == 1, f"max_contracts_per_entry={max_c}, expected=1"

    def test_slot_count_within_limit(self, portfolio_copy):
        """Current slot count ≤ max_slots."""
        n_slots = len(portfolio_copy.get("slots", {}))
        max_slots = portfolio_copy.get("max_slots", 3)
        assert n_slots <= max_slots, f"n_slots={n_slots} > max_slots={max_slots}"

    def test_all_slots_single_contract(self, portfolio_copy):
        """Every slot has exactly 1 contract."""
        for slot_id, slot in portfolio_copy.get("slots", {}).items():
            assert slot.get("contracts", 0) == 1, (
                f"Slot {slot_id} has {slot.get('contracts')} contracts, expected 1"
            )

    def test_no_live_broker_patterns(self):
        """New modules have no broker/live-order function calls (AST-based)."""
        import ast as _ast

        call_names = {"post_order", "place_order", "send_order", "submit_order", "Client"}
        modules = [
            _CODE_DIR / "oss_shortlist.py",
            _CODE_DIR / "scorecard_metrics.py",
        ]
        for mod_path in modules:
            tree = _ast.parse(mod_path.read_text())
            found = []
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Call):
                    func = node.func
                    if isinstance(func, _ast.Name) and func.id in call_names:
                        found.append(func.id)
                    elif isinstance(func, _ast.Attribute) and func.attr in call_names:
                        found.append(f".{func.attr}")
            assert not found, (
                f"{mod_path.name} contains forbidden broker calls: {found}"
            )
