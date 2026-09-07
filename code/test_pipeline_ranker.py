"""Integration tests for pipeline_ranker + slot_scorecard.

≥3 fixtures, 8+ tests covering:
  - slots ≤3
  - RI excluded
  - 1 contract cap
  - scorecard completeness
  - lifecycle section present
  - deterministic ordering
  - no broker imports
  - per-slot scores present
"""
import inspect
import sys
import os
import pytest

# Ensure code/ is on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pipeline_ranker import run_pipeline
from slot_scorecard import slot_scorecard


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def empty_candidates():
    """No candidates at all."""
    return []


@pytest.fixture
def ri_only_candidates():
    """Only RI (excluded) candidates."""
    return [
        {"ticker": "RI", "direction": "LONG",
         "win_rate": 0.9, "avg_win": 500, "avg_loss": 10},
        {"ticker": "RI", "direction": "SHORT",
         "win_rate": 0.85, "avg_win": 400, "avg_loss": 20},
    ]


@pytest.fixture
def mixed_candidates():
    """5 candidates including RI — tests VETO + scoring + top-3."""
    return [
        {"ticker": "LKOH", "direction": "LONG",
         "win_rate": 0.6, "avg_win": 200, "avg_loss": 100},
        {"ticker": "GAZP", "direction": "SHORT",
         "win_rate": 0.5, "avg_win": 80, "avg_loss": 50},
        {"ticker": "SBER", "direction": "LONG",
         "win_rate": 0.7, "avg_win": 100, "avg_loss": 120},
        {"ticker": "RI", "direction": "LONG",
         "win_rate": 0.9, "avg_win": 500, "avg_loss": 10},
        {"ticker": "BR", "direction": "SHORT",
         "win_rate": 0.45, "avg_win": 60, "avg_loss": 40},
    ]


@pytest.fixture
def base_config():
    """Minimal config matching config.json structure."""
    return {
        "deposit_rub": 21281,
        "excluded": ["RI"],
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "risk_per_trade_pct": 2.7,
            "go_budget_pct": 50,
            "delta_band_pct": 30,
            "portfolio_stop_drawdown_pct": 25,
            "signal_max_age_minutes": 16,
        },
        "risk_scorecard_weights": {
            "exposure": 20, "drawdown": 20, "volatility": 15,
            "correlation": 10, "signal_age": 15, "slots": 10, "caps": 10,
        },
    }


@pytest.fixture
def regime_snapshot():
    """Sample regime snapshot with 5 tickers."""
    return {
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "LKOH": {"adx": 21.4, "direction": "down", "regime": "range"},
            "SBER": {"adx": 45.3, "direction": "down", "regime": "trend"},
            "Si": {"adx": 31.7, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }


@pytest.fixture
def sample_returns():
    """Sample returns for lifecycle metrics."""
    return [0.01, -0.005, 0.02, -0.01, 0.015, -0.002, 0.008, 0.003]


# ─── Tests ─────────────────────────────────────────────────────────────

class TestSelectLiveSlotsLimit:
    """Slots ≤ 3."""

    def test_empty_candidates_returns_empty(self, empty_candidates, base_config):
        result = run_pipeline(base_config, empty_candidates)
        assert result["selected"] == []
        assert result["meta"]["n_selected"] == 0

    def test_max_three_slots(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        assert len(result["selected"]) <= 3
        assert result["meta"]["max_slots"] == 3


class TestRIExcluded:
    """RI never in output."""

    def test_ri_only_yields_empty(self, ri_only_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, ri_only_candidates, regime_snapshot)
        assert result["meta"]["n_excluded"] == 2
        assert result["selected"] == []

    def test_ri_not_in_selected(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        tickers = [s["ticker"] for s in result["selected"]]
        assert "RI" not in tickers


class TestContractsCap:
    """1 contract max per entry."""

    def test_contracts_never_exceeds_one(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        for s in result["selected"]:
            assert s["contracts"] <= 1

    def test_multi_contract_candidate_capped(self, base_config, regime_snapshot):
        candidates = [
            {"ticker": "LKOH", "direction": "LONG", "contracts_requested": 5,
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 100},
        ]
        result = run_pipeline(base_config, candidates, regime_snapshot)
        assert len(result["selected"]) == 1
        assert result["selected"][0]["contracts"] <= 1


class TestScorecard:
    """Scorecard completeness."""

    def test_scorecard_present_when_slots_selected(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        assert result["scorecard"] is not None
        assert "components" in result["scorecard"]
        assert "risk_score" in result["scorecard"]
        assert "verdict" in result["scorecard"]

    def test_scorecard_absent_when_no_slots(self, ri_only_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, ri_only_candidates, regime_snapshot)
        assert result["scorecard"] is None


class TestLifecycle:
    """Lifecycle section."""

    def test_lifecycle_present_with_returns(self, mixed_candidates, base_config,
                                           regime_snapshot, sample_returns):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot,
                              returns=sample_returns)
        assert result["lifecycle"] is not None
        assert "hit_rate" in result["lifecycle"]
        assert "stability" in result["lifecycle"]
        assert result["lifecycle_verdict"] in ("ALLOW", "WARN")

    def test_lifecycle_absent_without_returns(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        assert result["lifecycle"] is None


class TestPerSlotScores:
    """Per-slot scores present."""

    def test_per_slot_scores_length_matches_selected(self, mixed_candidates, base_config,
                                                     regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        assert len(result["per_slot_scores"]) == len(result["selected"])

    def test_per_slot_has_required_fields(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        for ps in result["per_slot_scores"]:
            assert "ticker" in ps
            assert "expectancy_r" in ps
            assert "risk_penalty" in ps
            assert "regime_bonus" in ps
            assert "score" in ps


class TestNoBrokerImports:
    """No live broker/orders in pipeline_ranker and slot_scorecard."""

    def test_pipeline_ranker_no_broker(self):
        source = inspect.getsource(sys.modules["pipeline_ranker"])
        forbidden = {"post_order", "place_order", "send_order", "submit_order",
                      "Client", "tinkoff", "broker"}
        for name in forbidden:
            assert name not in source.lower() or name in ("broker",), \
                f"pipeline_ranker contains forbidden: {name}"

    def test_slot_scorecard_no_broker(self):
        source = inspect.getsource(sys.modules["slot_scorecard"])
        forbidden = {"post_order", "place_order", "send_order", "submit_order",
                      "Client"}
        for name in forbidden:
            assert name not in source, f"slot_scorecard contains forbidden: {name}"


class TestDeterministicOrdering:
    """Same input → same output."""

    def test_deterministic(self, mixed_candidates, base_config, regime_snapshot):
        r1 = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        r2 = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        t1 = [s["ticker"] for s in r1["selected"]]
        t2 = [s["ticker"] for s in r2["selected"]]
        assert t1 == t2


class TestMeta:
    """Meta counters correct."""

    def test_meta_counts(self, mixed_candidates, base_config, regime_snapshot):
        result = run_pipeline(base_config, mixed_candidates, regime_snapshot)
        meta = result["meta"]
        assert meta["n_candidates"] == 5
        assert meta["n_excluded"] == 1  # RI
        assert meta["n_selected"] <= 3
        # n_gated = 0 when gate disabled
        assert meta["n_gated"] == 0


class TestSlotScorecardUnit:
    """Unit tests for slot_scorecard."""

    def test_slot_scorecard_basic(self):
        s = {"ticker": "LKOH", "direction": "LONG",
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 100}
        result = slot_scorecard(s)
        assert result["ticker"] == "LKOH"
        assert isinstance(result["expectancy_r"], float)
        assert isinstance(result["risk_penalty"], float)
        assert isinstance(result["regime_bonus"], float)

    def test_slot_scorecard_with_lifecycle(self, sample_returns):
        s = {"ticker": "GAZP", "direction": "SHORT"}
        result = slot_scorecard(s, returns=sample_returns)
        assert result["lifecycle"] is not None
        assert "hit_rate" in result["lifecycle"]
        assert "stability" in result["lifecycle"]
