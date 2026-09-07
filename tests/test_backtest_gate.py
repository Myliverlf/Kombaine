"""Tests for backtest_gate — ≥3 fixtures, covers all gate rules."""
import json
import os
import sys
import py_compile
import pytest

_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_CODE_DIR))

from backtest_gate import apply_gate

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ── Fixtures ──

@pytest.fixture
def fixture_all_pass():
    with open(os.path.join(FIXTURES_DIR, "backtest_gate_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "all_pass")


@pytest.fixture
def fixture_ri_excluded():
    with open(os.path.join(FIXTURES_DIR, "backtest_gate_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "ri_excluded")


@pytest.fixture
def fixture_threshold_rejection():
    with open(os.path.join(FIXTURES_DIR, "backtest_gate_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "threshold_rejection")


@pytest.fixture
def fixture_max_slots_cap():
    with open(os.path.join(FIXTURES_DIR, "backtest_gate_fixture.json")) as f:
        data = json.load(f)
    return next(x for x in data if x["name"] == "max_slots_cap")


@pytest.fixture
def fixture_empty():
    return {
        "gate_candidates": [],
        "gate_config": {
            "min_composite_score": 50.0,
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "excluded": ["RI"],
        },
    }


# ── Tests ──

class TestBacktestGate:
    def test_all_pass(self, fixture_all_pass):
        result = apply_gate(fixture_all_pass["gate_candidates"], fixture_all_pass["gate_config"])
        assert result["gate_meta"]["n_passed"] == fixture_all_pass["expected_n_passed"]
        assert result["gate_meta"]["n_rejected"] == fixture_all_pass["expected_n_rejected"]
        passed_tickers = [c["ticker"] for c in result["passed"]]
        assert passed_tickers == fixture_all_pass["expected_passed_tickers"]

    def test_ri_excluded(self, fixture_ri_excluded):
        result = apply_gate(fixture_ri_excluded["gate_candidates"], fixture_ri_excluded["gate_config"])
        assert result["gate_meta"]["n_passed"] == fixture_ri_excluded["expected_n_passed"]
        assert result["gate_meta"]["n_rejected"] == fixture_ri_excluded["expected_n_rejected"]
        passed_tickers = [c["ticker"] for c in result["passed"]]
        assert passed_tickers == fixture_ri_excluded["expected_passed_tickers"]
        # Verify RI is in rejected with correct reason
        rejected_reasons = [r["reason"] for r in result["rejected"]]
        assert any(fixture_ri_excluded["expected_rejected_reason_fragment"] in r for r in rejected_reasons)

    def test_threshold_rejection(self, fixture_threshold_rejection):
        result = apply_gate(fixture_threshold_rejection["gate_candidates"], fixture_threshold_rejection["gate_config"])
        assert result["gate_meta"]["n_passed"] == fixture_threshold_rejection["expected_n_passed"]
        assert result["gate_meta"]["n_rejected"] == fixture_threshold_rejection["expected_n_rejected"]
        passed_tickers = [c["ticker"] for c in result["passed"]]
        assert passed_tickers == fixture_threshold_rejection["expected_passed_tickers"]

    def test_max_slots_cap(self, fixture_max_slots_cap):
        result = apply_gate(fixture_max_slots_cap["gate_candidates"], fixture_max_slots_cap["gate_config"])
        assert result["gate_meta"]["n_passed"] == fixture_max_slots_cap["expected_n_passed"]
        assert result["gate_meta"]["n_rejected"] == fixture_max_slots_cap["expected_n_rejected"]
        # Top-3 by score survive
        passed_tickers = [c["ticker"] for c in result["passed"]]
        assert passed_tickers == fixture_max_slots_cap["expected_passed_tickers"]
        # Overflow rejected
        overflow_reasons = [r["reason"] for r in result["rejected"]]
        assert any("slot_cap_overflow" in r for r in overflow_reasons)

    def test_empty_input(self, fixture_empty):
        result = apply_gate(fixture_empty["gate_candidates"], fixture_empty["gate_config"])
        assert result["gate_meta"]["n_passed"] == 0
        assert result["gate_meta"]["n_rejected"] == 0
        assert result["passed"] == []

    def test_contracts_clamped(self):
        candidates = [{"ticker": "Si", "composite_score": 80.0, "contracts": 5}]
        config = {"min_composite_score": 50.0, "max_slots": 3, "max_contracts_per_entry": 1, "excluded": ["RI"]}
        result = apply_gate(candidates, config)
        assert len(result["passed"]) == 1
        assert result["passed"][0]["contracts"] == 1
        assert result["passed"][0].get("_gate_clamped") is True

    def test_gate_meta_structure(self, fixture_all_pass):
        result = apply_gate(fixture_all_pass["gate_candidates"], fixture_all_pass["gate_config"])
        meta = result["gate_meta"]
        assert "n_passed" in meta
        assert "n_rejected" in meta
        assert "threshold" in meta
        assert "max_slots" in meta
        assert "max_contracts_per_entry" in meta
        assert "excluded" in meta

    def test_none_config_defaults(self):
        candidates = [{"ticker": "Si", "composite_score": 80.0}]
        result = apply_gate(candidates, None)
        assert result["gate_meta"]["n_passed"] == 1
        assert result["gate_meta"]["threshold"] == 50.0

    def test_no_broker_imports(self):
        """Verify module has no broker-related imports."""
        gate_path = os.path.join(_CODE_DIR, "backtest_gate.py")
        py_compile.compile(gate_path, doraise=True)
        with open(gate_path) as f:
            source = f.read()
        broker_keywords = ["tinkoff", "futures_lab", "broker_client", "place_order", "send_order"]
        for kw in broker_keywords:
            assert kw not in source, f"Broker keyword '{kw}' found in backtest_gate.py"
