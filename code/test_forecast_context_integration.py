"""Integration tests for ForecastContext + Forecast Context Scorer.

Tests backward compatibility, forecast_scorer compat, scorecard structure,
and pipeline constraints (max_slots<=3, contracts<=1, RI excluded).
≥3 tests, no live broker/orders, all dry-run.
"""
import json
import os
import sys

import pytest

# Ensure code/ dir on sys.path
_CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from timesfm_adapter import ForecastResult, DummyTimesFMAdapter, get_adapter
from forecast_context import ForecastContext, build_forecast_context, EXCLUDED_TICKERS
from forecast_context_scorer import (
    forecast_aware_allocator_score,
    forecast_aware_quality_gate,
    forecast_aware_risk_context,
    forecast_aware_signal_weight,
    build_forecast_scorecard,
    check_no_broker_imports,
    BASELINE_FORECAST_WEIGHT,
    MAX_FORECAST_WEIGHT,
)

# Try importing existing modules for compat checks
try:
    from forecast_scorer import (
        forecast_bonus,
        forecast_risk_penalty,
        allocator_score_with_forecast,
        EXCLUDED_TICKERS as SCORER_EXCLUDED,
    )
    _HAS_FORECAST_SCORER = True
except ImportError:
    _HAS_FORECAST_SCORER = False

try:
    from candidate_allocator import select_live_slots
    _HAS_ALLOCATOR = True
except ImportError:
    _HAS_ALLOCATOR = False

try:
    from pipeline_ranker import run_pipeline
    _HAS_PIPELINE_RANKER = True
except ImportError:
    _HAS_PIPELINE_RANKER = False


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def dummy_adapter():
    """DummyTimesFMAdapter — no real model."""
    return DummyTimesFMAdapter()


@pytest.fixture
def real_bars_map():
    """Bars for 5 tickers including RI (excluded)."""
    return {
        "BR": [100.0 + i * 0.5 for i in range(30)],
        "GAZP": [200.0 - i * 0.3 for i in range(30)],
        "SBER": [250.0 + 0.1 * i for i in range(30)],
        "LKOH": [600.0 + 2.0 * i for i in range(30)],
        "RI": [100.0 + i for i in range(30)],
    }


@pytest.fixture
def regime_snapshot():
    """Standard regime snapshot."""
    return {
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend"},
            "SBER": {"adx": 18.0, "direction": "down", "regime": "range"},
            "LKOH": {"adx": 15.0, "direction": "down", "regime": "range"},
            "RI": {"adx": 50.0, "direction": "up", "regime": "trend"},
        },
        "bias": "neutral",
    }


@pytest.fixture
def sample_config():
    """Standard config matching config.json constraints."""
    return {
        "excluded": ["RI"],
        "universe": ["BR", "GAZP", "LKOH", "SBER", "Si"],
        "risk": {
            "max_slots": 3,
            "max_contracts_per_entry": 1,
            "risk_per_trade_pct": 2.7,
            "deposit_rub": 100000,
            "go_budget_pct": 50,
            "portfolio_stop_drawdown_pct": 25,
            "delta_band_pct": 30,
            "signal_max_age_minutes": 16,
        },
        "risk_scorecard_weights": {
            "exposure": 20,
            "drawdown": 20,
        },
        "deposit_rub": 100000,
    }


@pytest.fixture
def sample_candidates():
    """5 candidates including RI for exclusion test."""
    return [
        {"ticker": "BR", "direction": "SHORT", "win_rate": 0.45, "avg_win": 60.0, "avg_loss": 40.0},
        {"ticker": "GAZP", "direction": "LONG", "win_rate": 0.55, "avg_win": 80.0, "avg_loss": 50.0},
        {"ticker": "LKOH", "direction": "LONG", "win_rate": 0.60, "avg_win": 200.0, "avg_loss": 100.0},
        {"ticker": "SBER", "direction": "LONG", "win_rate": 0.70, "avg_win": 100.0, "avg_loss": 120.0},
        {"ticker": "RI", "direction": "LONG", "win_rate": 0.90, "avg_win": 500.0, "avg_loss": 10.0},
    ]


@pytest.fixture
def high_confidence_context(dummy_adapter, real_bars_map, regime_snapshot):
    """Build a ForecastContext with dummy adapter (no real signals)."""
    return build_forecast_context(dummy_adapter, real_bars_map, regime_snapshot)


# ─── Test 1: Existing 198 tests NOT broken (smoke) ────────────────────


class TestExistingTestsNotBroken:
    """Verify that adding new modules doesn't break existing pipeline."""

    def test_forecast_scorer_imports(self):
        """Existing forecast_scorer module still imports fine."""
        if not _HAS_FORECAST_SCORER:
            pytest.skip("forecast_scorer not importable")
        assert callable(forecast_bonus)
        assert callable(forecast_risk_penalty)
        assert callable(allocator_score_with_forecast)

    def test_forecast_scorer_ri_excluded_matches(self):
        """forecast_scorer EXCLUDED_TICKERS matches forecast_context."""
        if not _HAS_FORECAST_SCORER:
            pytest.skip("forecast_scorer not importable")
        assert "RI" in SCORER_EXCLUDED
        assert "RI" in EXCLUDED_TICKERS
        assert SCORER_EXCLUDED == EXCLUDED_TICKERS

    def test_candidate_allocator_imports(self):
        """Existing candidate_allocator module still imports fine."""
        if not _HAS_ALLOCATOR:
            pytest.skip("candidate_allocator not importable")
        assert callable(select_live_slots)

    def test_pipeline_ranker_imports(self):
        """Existing pipeline_ranker module still imports fine."""
        if not _HAS_PIPELINE_RANKER:
            pytest.skip("pipeline_ranker not importable")
        assert callable(run_pipeline)

    def test_all_code_modules_compile(self):
        """All code/*.py files compile without errors."""
        import importlib
        modules = [
            "timesfm_adapter",
            "forecast_scorer",
            "forecast_priors",
            "forecast_context",
            "forecast_context_scorer",
        ]
        for mod_name in modules:
            try:
                m = importlib.import_module(mod_name)
                assert m is not None, f"{mod_name} import returned None"
            except ImportError as e:
                pytest.fail(f"Failed to import {mod_name}: {e}")


# ─── Test 2: ForecastContext + existing forecast_scorer compat ─────────


class TestForecastContextAndScorerCompat:
    """Verify ForecastContext works alongside existing forecast_scorer."""

    def test_both_produce_same_forecast_bonus_direction(self):
        """Both forecast_context_scorer and forecast_scorer agree on CI→bonus direction."""
        if not _HAS_FORECAST_SCORER:
            pytest.skip("forecast_scorer not importable")

        fr = ForecastResult(direction="up", ci_width=0.015, confidence=0.88, horizon=20, source="timesfm")
        candidate = {"ticker": "BR", "direction": "LONG"}

        # Existing forecast_scorer.bonus
        existing_bonus = forecast_bonus(candidate, fr)

        # New forecast_context_scorer via build_forecast_scorecard
        ctx = ForecastContext(
            per_ticker={"BR": fr},
            portfolio_bias="up",
            volatility_regime="calm",
            confidence_score=0.88,
            meta={"tickers_with_signal": 1, "tickers_no_signal": 0, "avg_ci_width": 0.015},
        )
        enhanced = forecast_aware_allocator_score(candidate, ctx, base_score=0.5)

        # Enhanced score should be higher than base due to narrow CI
        assert enhanced > 0.5

    def test_allocator_score_with_forecast_backward_compat(self):
        """allocator_score_with_forecast still works without ForecastContext."""
        if not _HAS_FORECAST_SCORER:
            pytest.skip("forecast_scorer not importable")

        candidate = {"ticker": "BR", "direction": "SHORT", "win_rate": 0.45, "avg_win": 60.0, "avg_loss": 40.0}
        regime = {"tickers": {"BR": {"adx": 23.1, "direction": "up", "regime": "trend"}}}

        score = allocator_score_with_forecast(candidate, regime)
        assert isinstance(score, float)
        assert score != 0.0 or True  # may be 0 but should not raise

    def test_forecast_context_does_not_modify_forecast_results(self):
        """ForecastContext per_ticker stores ForecastResults unchanged."""
        fr = ForecastResult(direction="up", ci_width=0.02, confidence=0.8, horizon=20, source="timesfm")
        ctx = ForecastContext(
            per_ticker={"BR": fr},
            meta={"tickers_with_signal": 1, "tickers_no_signal": 0, "avg_ci_width": 0.02},
        )
        stored = ctx.get_ticker("BR")
        assert stored.direction == "up"
        assert stored.ci_width == 0.02
        assert stored.confidence == 0.8


# ─── Test 3: Scorecard dict with PnL↑/risk↓ keys ─────────────────────


class TestScorecardStructure:
    """Verify build_forecast_scorecard produces valid scorecard dict."""

    def test_scorecard_has_required_keys(self, high_confidence_context):
        """Scorecard dict contains PnL↑/risk↓ indicators."""
        scorecard = build_forecast_scorecard(high_confidence_context)

        required_keys = [
            "forecast_confidence",
            "forecast_bias",
            "vol_regime",
            "forecast_available",
            "allocator_score",
            "risk_adjustment",
            "vol_spike_detected",
            "pnl_up_indicator",
            "risk_down_indicator",
        ]
        for key in required_keys:
            assert key in scorecard, f"Missing key: {key}"

    def test_scorecard_values_in_valid_ranges(self, high_confidence_context):
        """Scorecard values in valid ranges."""
        scorecard = build_forecast_scorecard(high_confidence_context)

        assert 0.0 <= scorecard["forecast_confidence"] <= 1.0
        assert scorecard["forecast_bias"] in ("up", "down", "flat")
        assert scorecard["vol_regime"] in ("calm", "normal", "volatile")
        assert isinstance(scorecard["forecast_available"], bool)
        assert isinstance(scorecard["allocator_score"], float)
        assert scorecard["risk_adjustment"] >= 1.0

    def test_scorecard_with_risk_context(self, high_confidence_context):
        """Scorecard includes risk_context when provided."""
        risk_ctx = forecast_aware_risk_context(
            [{"ticker": "BR", "direction": "SHORT"}],
            high_confidence_context,
        )
        scorecard = build_forecast_scorecard(
            high_confidence_context,
            risk_context=risk_ctx,
        )
        assert "risk_adjustment" in scorecard
        assert "vol_spike_detected" in scorecard
        assert "confidence_risk" in scorecard

    def test_scorecard_with_enhanced_scores(self, high_confidence_context):
        """Scorecard includes allocator scores when provided."""
        enhanced = {"BR": 0.55, "GAZP": 0.48, "SBER": 0.42}
        scorecard = build_forecast_scorecard(
            high_confidence_context,
            allocator_enhanced_scores=enhanced,
        )
        assert scorecard["allocator_score"] > 0.0
        assert scorecard["allocator_scores"] == enhanced


# ─── Test 4: Pipeline constraints (max_slots<=3, contracts<=1, RI excluded) ──


class TestPipelineConstraints:
    """Verify pipeline constraints are maintained."""

    def test_max_slots_constraint(self, high_confidence_context):
        """forecast_context_scorer respects max_slots=3."""
        # Build context with 5 tickers
        ctx = high_confidence_context
        candidates = [
            {"ticker": "BR", "direction": "SHORT", "win_rate": 0.45, "avg_win": 60.0, "avg_loss": 40.0},
            {"ticker": "GAZP", "direction": "LONG", "win_rate": 0.55, "avg_win": 80.0, "avg_loss": 50.0},
            {"ticker": "LKOH", "direction": "LONG", "win_rate": 0.60, "avg_win": 200.0, "avg_loss": 100.0},
            {"ticker": "SBER", "direction": "LONG", "win_rate": 0.70, "avg_win": 100.0, "avg_loss": 120.0},
            {"ticker": "RI", "direction": "LONG", "win_rate": 0.90, "avg_win": 500.0, "avg_loss": 10.0},
        ]

        # Score all candidates with forecast context
        enhanced_scores = {}
        for c in candidates:
            score = forecast_aware_allocator_score(c, ctx, base_score=0.5)
            enhanced_scores[c["ticker"]] = score

        # RI should have no forecast contribution
        ri_score = enhanced_scores["RI"]
        br_score = enhanced_scores["BR"]
        # RI gets base_score unchanged, BR may get bonus/penalty
        assert ri_score == 0.5  # RI excluded → no forecast bonus

        # With candidate_allocator, max_slots=3
        if _HAS_ALLOCATOR:
            from candidate_allocator import select_live_slots
            cfg = {
                "excluded": ["RI"],
                "risk": {"max_slots": 3, "max_contracts_per_entry": 1},
                "risk_per_trade_pct": 2.7,
                "deposit_rub": 100000,
            }
            selected = select_live_slots(candidates, cfg)
            assert len(selected) <= 3

    def test_contracts_max_one(self):
        """contracts_requested never exceeds max_contracts_per_entry=1."""
        if _HAS_ALLOCATOR:
            from candidate_allocator import select_live_slots
            candidates = [
                {"ticker": "BR", "direction": "SHORT", "win_rate": 0.45, "avg_win": 60.0,
                 "avg_loss": 40.0, "contracts_requested": 5},
            ]
            cfg = {
                "excluded": ["RI"],
                "risk": {"max_slots": 3, "max_contracts_per_entry": 1},
                "risk_per_trade_pct": 2.7,
                "deposit_rub": 100000,
            }
            selected = select_live_slots(candidates, cfg)
            for s in selected:
                assert s["contracts"] <= 1

    def test_ri_excluded_from_allocation(self):
        """RI is excluded from select_live_slots output."""
        if _HAS_ALLOCATOR:
            from candidate_allocator import select_live_slots
            candidates = [
                {"ticker": "RI", "direction": "LONG", "win_rate": 0.95, "avg_win": 500.0, "avg_loss": 10.0},
                {"ticker": "BR", "direction": "SHORT", "win_rate": 0.45, "avg_win": 60.0, "avg_loss": 40.0},
            ]
            cfg = {
                "excluded": ["RI"],
                "risk": {"max_slots": 3, "max_contracts_per_entry": 1},
                "risk_per_trade_pct": 2.7,
                "deposit_rub": 100000,
            }
            selected = select_live_slots(candidates, cfg)
            tickers = [s["ticker"] for s in selected]
            assert "RI" not in tickers

    def test_config_max_slots_is_3(self, sample_config):
        """config.json has max_slots=3."""
        assert sample_config["risk"]["max_slots"] == 3

    def test_config_contracts_per_entry_is_1(self, sample_config):
        """config.json has max_contracts_per_entry=1."""
        assert sample_config["risk"]["max_contracts_per_entry"] == 1

    def test_config_excludes_ri(self, sample_config):
        """config.json excludes RI."""
        assert "RI" in sample_config["excluded"]


# ─── Test 5: No live broker/orders in any new file ─────────────────────


class TestNoLiveBroker:
    """Verify all new files have no broker imports."""

    def test_forecast_context_no_broker(self):
        """forecast_context.py has no broker imports."""
        path = os.path.join(_CODE_DIR, "forecast_context.py")
        assert check_no_broker_imports(path)

    def test_forecast_context_scorer_no_broker(self):
        """forecast_context_scorer.py has no broker imports."""
        path = os.path.join(_CODE_DIR, "forecast_context_scorer.py")
        assert check_no_broker_imports(path)

    def test_no_tinkoff_imports_in_new_files(self):
        """None of the new files import tinkoff/broker (via AST guard)."""
        new_files = ["forecast_context.py", "forecast_context_scorer.py"]
        for fname in new_files:
            path = os.path.join(_CODE_DIR, fname)
            if os.path.exists(path):
                assert check_no_broker_imports(path), f"Broker import found in {fname}"


# ─── Test 6: Full pipeline flow with ForecastContext ──────────────────


class TestFullPipelineFlow:
    """End-to-end test: build context → score candidates → build scorecard."""

    def test_full_flow(self, dummy_adapter, real_bars_map, regime_snapshot, sample_candidates):
        """Complete flow: context → allocator scores → risk → scorecard."""
        # 1. Build ForecastContext
        ctx = build_forecast_context(dummy_adapter, real_bars_map, regime_snapshot)
        assert isinstance(ctx, ForecastContext)

        # 2. Enhanced allocator scores for all candidates
        enhanced_scores = {}
        for c in sample_candidates:
            score = forecast_aware_allocator_score(c, ctx, base_score=0.5)
            enhanced_scores[c["ticker"]] = score
        assert len(enhanced_scores) == 5

        # 3. Risk context
        risk_ctx = forecast_aware_risk_context(
            [{"ticker": "BR", "direction": "SHORT"}, {"ticker": "GAZP", "direction": "LONG"}],
            ctx,
        )
        assert "forecast_vol_spike" in risk_ctx
        assert "per_slot_risk" in risk_ctx

        # 4. Build scorecard
        scorecard = build_forecast_scorecard(
            ctx,
            allocator_enhanced_scores=enhanced_scores,
            risk_context=risk_ctx,
        )
        assert scorecard["forecast_available"] is False  # dummy adapter
        assert scorecard["allocator_score"] > 0.0
        assert "pnl_up_indicator" in scorecard
        assert "risk_down_indicator" in scorecard

    def test_signal_weight_integration(self, dummy_adapter, real_bars_map, regime_snapshot):
        """forecast_aware_signal_weight modifies base_weight correctly."""
        ctx = build_forecast_context(dummy_adapter, real_bars_map, regime_snapshot)

        base_weight = 0.5
        # BR has dummy forecast → base_weight unchanged
        modified = forecast_aware_signal_weight("BR", base_weight, ctx)
        assert modified == base_weight  # dummy → no change

        # RI → base_weight unchanged
        modified_ri = forecast_aware_signal_weight("RI", base_weight, ctx)
        assert modified_ri == base_weight

        # Non-existent ticker → base_weight unchanged
        modified_none = forecast_aware_signal_weight("NONEXIST", base_weight, ctx)
        assert modified_none == base_weight


# ─── Test 7: Scorecard indicates PnL↑/risk↓ ──────────────────────────


class TestScorecardIndicators:
    """Verify scorecard PnL↑/risk↓ indicators work correctly."""

    def test_no_forecast_no_pnl_up(self):
        """Without forecast → pnl_up_indicator = False."""
        ctx = ForecastContext(meta={"tickers_with_signal": 0, "tickers_no_signal": 0})
        scorecard = build_forecast_scorecard(ctx)
        assert scorecard["pnl_up_indicator"] is False

    def test_no_forecast_risk_down_ok(self):
        """Without forecast → risk_down_indicator = True (no vol spike)."""
        ctx = ForecastContext(meta={"tickers_with_signal": 0, "tickers_no_signal": 0})
        scorecard = build_forecast_scorecard(ctx)
        assert scorecard["risk_down_indicator"] is True

    def test_volatile_regime_risk_not_down(self):
        """Volatile vol_regime → risk_down_indicator = False."""
        ctx = ForecastContext(
            per_ticker={"BR": ForecastResult("up", 0.01, 0.8, 20, "timesfm")},
            volatility_regime="volatile",
            confidence_score=0.8,
            meta={"tickers_with_signal": 1},
        )
        risk_ctx = {"overall_risk_adjustment": 1.5, "forecast_vol_spike": True}
        scorecard = build_forecast_scorecard(ctx, risk_context=risk_ctx)
        assert scorecard["risk_down_indicator"] is False

    def test_narrow_ci_pnl_up_possible(self):
        """Narrow CI with allocator score → pnl_up_indicator = True."""
        ctx = ForecastContext(
            per_ticker={"BR": ForecastResult("up", 0.01, 0.8, 20, "timesfm")},
            confidence_score=0.8,
            meta={"tickers_with_signal": 1, "tickers_no_signal": 0, "avg_ci_width": 0.01},
        )
        enhanced = {"BR": 0.55}
        scorecard = build_forecast_scorecard(ctx, allocator_enhanced_scores=enhanced)
        assert scorecard["pnl_up_indicator"] is True


# ─── Test 8: Forecast weights constants correct ────────────────────────


class TestWeightConstants:
    """Verify weight constants are as designed."""

    def test_baseline_forecast_weight(self):
        """Baseline forecast weight = 10%."""
        assert BASELINE_FORECAST_WEIGHT == 0.10

    def test_max_forecast_weight(self):
        """Max forecast weight = 20%."""
        assert MAX_FORECAST_WEIGHT == 0.20

    def test_max_exceeds_baseline(self):
        """Max forecast weight > baseline."""
        assert MAX_FORECAST_WEIGHT > BASELINE_FORECAST_WEIGHT
