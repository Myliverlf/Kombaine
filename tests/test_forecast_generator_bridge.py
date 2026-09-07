"""Tests for Forecast Generator Bridge — TimesFM pre-filter for strategy generator.

≥3 fixtures, все тесты проверяют:
  1. forecast_prefilter: high confidence → direction filter
  2. forecast_prefilter: low confidence → fail-open (pass all)
  3. forecast_prefilter: RI excluded
  4. forecast_aware_idea_score: aligned > baseline, misaligned < baseline
  5. forecast_aware_suggest_templates: confident → narrowed, low → standard
  6. run_forecast_quality_gate: end-to-end chain
  7. AST-guard: no broker imports

No live broker/orders. All fixtures are in-memory / from tests/fixtures/.
"""
import json
import math
import os
import sys

import pytest

# Ensure code/ is on sys.path
CODE_DIR = os.path.join(os.path.dirname(__file__), "..", "code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(CODE_DIR))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

from timesfm_adapter import ForecastResult, DummyTimesFMAdapter
from forecast_context import ForecastContext, build_forecast_context
from forecast_generator_bridge import (
    forecast_prefilter,
    forecast_aware_idea_score,
    forecast_aware_suggest_templates,
    run_forecast_quality_gate,
    check_no_broker_imports,
)
from strategy_ideas import idea_score, suggest_templates, ALL_TEMPLATES


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def ideas_fixture():
    """Load standard ideas from strategy_ideas_fixture.json."""
    path = os.path.join(FIXTURES_DIR, "strategy_ideas_fixture.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["ideas"]


@pytest.fixture
def config_fixture():
    """Standard config: excluded=["RI"], max_slots=3, max_contracts=1."""
    return {
        "excluded": ["RI"],
        "max_slots": 3,
        "max_contracts_per_entry": 1,
    }


@pytest.fixture
def regime_fixture():
    """Load regime_snapshot from fixture."""
    path = os.path.join(FIXTURES_DIR, "regime_snapshot.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def forecast_context_high_confidence():
    """ForecastContext with high confidence (0.75), GAZP=up, SBER=down, BR=up.

    GAZP LONG idea should pass; GAZP SHORT should be rejected.
    """
    per_ticker = {
        "GAZP": ForecastResult(direction="up", ci_width=0.03, confidence=0.8, horizon=20, source="timesfm"),
        "SBER": ForecastResult(direction="down", ci_width=0.05, confidence=0.7, horizon=20, source="timesfm"),
        "BR": ForecastResult(direction="up", ci_width=0.04, confidence=0.75, horizon=20, source="timesfm"),
        "LKOH": ForecastResult(direction="flat", ci_width=0.07, confidence=0.65, horizon=20, source="timesfm"),
    }
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias="up",
        volatility_regime="normal",
        confidence_score=0.75,
        meta={
            "tickers_with_signal": 4,
            "tickers_no_signal": 0,
            "avg_ci_width": 0.0475,
            "n_tickers": 4,
        },
    )


@pytest.fixture
def forecast_context_low_confidence():
    """ForecastContext with low confidence (0.15) — should trigger fail-open."""
    per_ticker = {
        "GAZP": ForecastResult(direction="up", ci_width=0.12, confidence=0.15, horizon=20, source="dummy"),
        "SBER": ForecastResult(direction="down", ci_width=0.10, confidence=0.12, horizon=20, source="dummy"),
    }
    return ForecastContext(
        per_ticker=per_ticker,
        portfolio_bias="flat",
        volatility_regime="volatile",
        confidence_score=0.15,
        meta={
            "tickers_with_signal": 0,
            "tickers_no_signal": 2,
            "avg_ci_width": 0.0,
            "n_tickers": 2,
        },
    )


@pytest.fixture
def ideas_with_ri():
    """Ideas list that includes RI ticker (should be veto'd)."""
    return [
        {"ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG",
         "win_rate": 0.6, "avg_win": 200, "avg_loss": 80},
        {"ticker": "RI", "strategy_name": "ema_cross", "direction": "LONG",
         "win_rate": 0.6, "avg_win": 200, "avg_loss": 80},
        {"ticker": "BR", "strategy_name": "ft_supertrend", "direction": "SHORT",
         "win_rate": 0.55, "avg_win": 180, "avg_loss": 90},
    ]


# ─── Tests ────────────────────────────────────────────────────────────

class TestForecastPrefilter:

    def test_high_confidence_aligned_passes(
        self, forecast_context_high_confidence, config_fixture
    ):
        """GAZP LONG idea should PASS when forecast says GAZP=up (aligned)."""
        ideas = [
            {"ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG",
             "win_rate": 0.6, "avg_win": 200, "avg_loss": 80},
        ]
        result = forecast_prefilter(ideas, forecast_context_high_confidence, config_fixture)
        assert len(result["passed"]) == 1
        assert result["passed"][0]["ticker"] == "GAZP"
        assert result["passed"][0].get("_forecast_boosted") is True
        assert result["meta"]["n_forecast_boosted"] == 1
        assert result["meta"]["n_rejected_forecast"] == 0

    def test_high_confidence_misaligned_rejected(
        self, forecast_context_high_confidence, config_fixture
    ):
        """GAZP SHORT idea should be REJECTED when forecast says GAZP=up."""
        ideas = [
            {"ticker": "GAZP", "strategy_name": "mean_reversion", "direction": "SHORT",
             "win_rate": 0.5, "avg_win": 100, "avg_loss": 120},
        ]
        result = forecast_prefilter(ideas, forecast_context_high_confidence, config_fixture)
        assert len(result["passed"]) == 0
        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["_reject_reason"].startswith("forecast_direction_mismatch")
        assert result["meta"]["n_rejected_forecast"] == 1

    def test_low_confidence_fail_open(self, forecast_context_low_confidence, config_fixture):
        """Low confidence → fail-open: all ideas pass regardless of direction."""
        ideas = [
            {"ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG"},
            {"ticker": "SBER", "strategy_name": "bollinger", "direction": "SHORT"},
        ]
        result = forecast_prefilter(ideas, forecast_context_low_confidence, config_fixture)
        assert len(result["passed"]) == 2
        assert len(result["rejected"]) == 0
        assert result["meta"]["fail_open"] is True

    def test_ri_excluded(
        self, forecast_context_high_confidence, config_fixture, ideas_with_ri
    ):
        """RI ideas should always be rejected (veto)."""
        result = forecast_prefilter(ideas_with_ri, forecast_context_high_confidence, config_fixture)
        ri_rejected = [
            r for r in result["rejected"]
            if r.get("ticker") == "RI" and r.get("_reject_reason") == "veto_excluded_ticker"
        ]
        assert len(ri_rejected) == 1
        assert result["meta"]["n_rejected_veto"] == 1

    def test_max_slots_enforced(self, forecast_context_high_confidence, config_fixture):
        """More ideas than max_slots → truncated."""
        ideas = [
            {"ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG", "score": 0.5},
            {"ticker": "BR", "strategy_name": "donchian", "direction": "LONG", "score": 0.4},
            {"ticker": "SBER", "strategy_name": "bollinger", "direction": "DOWN", "score": 0.3},
            {"ticker": "LKOH", "strategy_name": "rsi", "direction": "LONG", "score": 0.2},
        ]
        # max_slots=3 in config
        result = forecast_prefilter(ideas, forecast_context_high_confidence, config_fixture)
        assert len(result["passed"]) <= 3

    def test_none_context_passthrough(self, config_fixture):
        """None forecast_context → all ideas pass."""
        ideas = [{"ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG"}]
        result = forecast_prefilter(ideas, None, config_fixture)
        assert len(result["passed"]) == 1
        assert result["meta"]["fail_open"] is True


class TestForecastAwareIdeaScore:

    def test_aligned_score_higher_than_baseline(
        self, forecast_context_high_confidence, regime_fixture
    ):
        """Forecast-aligned idea should score higher than baseline (no forecast)."""
        idea = {
            "ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG",
            "win_rate": 0.6, "avg_win": 200, "avg_loss": 80,
            "drawdown_pct": 3.0, "volatility": 2.0,
        }
        baseline = idea_score(idea, regime_fixture, None, None)
        enhanced = forecast_aware_idea_score(
            idea, forecast_context_high_confidence, regime_fixture, None, None
        )
        # Both should be valid scores
        assert not math.isinf(baseline)
        assert not math.isinf(enhanced)
        # Enhanced should be >= baseline for aligned forecast
        assert enhanced >= baseline

    def test_misaligned_score_lower_than_baseline(
        self, forecast_context_high_confidence, regime_fixture
    ):
        """Forecast-misaligned idea should score lower than baseline."""
        idea = {
            "ticker": "SBER", "strategy_name": "bollinger_squeeze", "direction": "LONG",
            "win_rate": 0.45, "avg_win": 100, "avg_loss": 120,
            "drawdown_pct": 8.0, "volatility": 5.0,
        }
        # SBER forecast is down, idea is LONG → misaligned
        baseline = idea_score(idea, regime_fixture, None, None)
        enhanced = forecast_aware_idea_score(
            idea, forecast_context_high_confidence, regime_fixture, None, None
        )
        assert not math.isinf(baseline)
        assert not math.isinf(enhanced)
        # Misaligned should penalize
        assert enhanced <= baseline

    def test_veto_stays_veto(
        self, forecast_context_high_confidence, regime_fixture, config_fixture
    ):
        """RI idea should remain VETO'd even with forecast."""
        idea = {
            "ticker": "RI", "strategy_name": "ema_cross", "direction": "LONG",
            "win_rate": 0.7, "avg_win": 300, "avg_loss": 50,
        }
        score = forecast_aware_idea_score(
            idea, forecast_context_high_confidence, regime_fixture, None, config_fixture
        )
        assert math.isinf(score) and score < 0

    def test_no_forecast_returns_baseline(self, regime_fixture):
        """No forecast_context → baseline score from idea_score."""
        idea = {
            "ticker": "GAZP", "strategy_name": "ema_cross", "direction": "LONG",
            "win_rate": 0.6, "avg_win": 200, "avg_loss": 80,
        }
        baseline = idea_score(idea, regime_fixture, None, None)
        enhanced = forecast_aware_idea_score(idea, None, regime_fixture, None, None)
        assert enhanced == baseline

    def test_flat_forecast_no_penalty(
        self, forecast_context_high_confidence, regime_fixture
    ):
        """LKOH has flat forecast → no direction penalty or boost."""
        idea = {
            "ticker": "LKOH", "strategy_name": "mean_reversion", "direction": "LONG",
            "win_rate": 0.5, "avg_win": 150, "avg_loss": 100,
        }
        # LKOH forecast is flat → alignment = 0 → no forecast contribution
        baseline = idea_score(idea, regime_fixture, None, None)
        enhanced = forecast_aware_idea_score(
            idea, forecast_context_high_confidence, regime_fixture, None, None
        )
        # Should be approximately equal (flat forecast = no signal)
        assert abs(enhanced - baseline) < 0.01


class TestForecastAwareSuggestTemplates:

    def test_confident_up_bias_narrows(self, forecast_context_high_confidence, regime_fixture):
        """High confidence + bias=up → prefer trend templates."""
        templates = forecast_aware_suggest_templates(regime_fixture, forecast_context_high_confidence)
        # Should contain trend templates
        assert any(t in templates for t in ["ema_cross", "donchian", "ft_supertrend"])
        # Should be a subset of ALL_TEMPLATES
        assert all(t in ALL_TEMPLATES for t in templates)
        # Should be fewer than ALL_TEMPLATES
        assert len(templates) < len(ALL_TEMPLATES)

    def test_low_confidence_returns_standard(self, forecast_context_low_confidence, regime_fixture):
        """Low confidence → standard regime-based suggestions."""
        forecast_templates = forecast_aware_suggest_templates(regime_fixture, forecast_context_low_confidence)
        standard_templates = suggest_templates(regime_fixture)
        # Low confidence should delegate to standard
        assert forecast_templates == standard_templates

    def test_no_context_returns_standard(self, regime_fixture):
        """None context → standard."""
        result = forecast_aware_suggest_templates(regime_fixture, None)
        expected = suggest_templates(regime_fixture)
        assert result == expected

    def test_flat_bias_balanced_mix(self, regime_fixture):
        """Flat bias → balanced mix from trend + range."""
        ctx = ForecastContext(
            per_ticker={},
            portfolio_bias="flat",
            volatility_regime="normal",
            confidence_score=0.8,
            meta={"tickers_with_signal": 2, "tickers_no_signal": 0,
                  "avg_ci_width": 0.03, "n_tickers": 2},
        )
        templates = forecast_aware_suggest_templates(regime_fixture, ctx)
        assert 2 <= len(templates) <= 4


class TestRunForecastQualityGate:

    def test_end_to_end_with_fixture_ideas(
        self, forecast_context_high_confidence, ideas_fixture, regime_fixture, config_fixture
    ):
        """Full quality gate with fixture ideas and high confidence forecast."""
        feedback = {
            "LONG": {"pnl": -257},
            "SHORT": {"pnl": 726},
            "best_hours": [8, 7],
        }
        result = run_forecast_quality_gate(
            ideas_fixture,
            forecast_context_high_confidence,
            regime_fixture,
            feedback,
            config_fixture,
        )
        assert "passed" in result
        assert "rejected" in result
        assert "meta" in result
        # Meta should have forecast stats
        assert "n_forecast_boosted" in result["meta"]
        assert "n_rejected_forecast" in result["meta"]
        assert "n_rejected_veto" in result["meta"]
        # All passed ideas should have contracts=1
        for idea in result["passed"]:
            assert idea.get("contracts") == 1
        # No more than max_slots
        assert len(result["passed"]) <= config_fixture["max_slots"]

    def test_ri_veto_in_full_gate(self, ideas_with_ri, forecast_context_high_confidence, config_fixture):
        """RI ideas should be veto'd in full quality gate."""
        result = run_forecast_quality_gate(
            ideas_with_ri, forecast_context_high_confidence, None, None, config_fixture
        )
        ri_in_passed = [i for i in result["passed"] if i.get("ticker") == "RI"]
        assert len(ri_in_passed) == 0
        assert result["meta"]["n_rejected_veto"] >= 1

    def test_empty_ideas(self, forecast_context_high_confidence, config_fixture):
        """Empty ideas list → empty result."""
        result = run_forecast_quality_gate(
            [], forecast_context_high_confidence, None, None, config_fixture
        )
        assert result["passed"] == []
        assert result["meta"]["n_total"] == 0


class TestASTGuard:

    def test_no_broker_imports_in_bridge(self):
        """forecast_generator_bridge.py should have no broker imports."""
        bridge_path = os.path.join(CODE_DIR, "forecast_generator_bridge.py")
        assert os.path.exists(bridge_path), f"Bridge file not found: {bridge_path}"
        assert check_no_broker_imports(bridge_path) is True


class TestForecastContextBuildFromFixture:

    def test_build_from_fixture_bars(self):
        """Build ForecastContext from forecast_context_fixture.json bars."""
        fixture_path = os.path.join(FIXTURES_DIR, "forecast_context_fixture.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            fixture = json.load(f)

        adapter = DummyTimesFMAdapter()
        ctx = build_forecast_context(
            adapter, fixture["bars_map"], fixture.get("regime_snapshot")
        )
        assert isinstance(ctx, ForecastContext)
        # RI should be excluded from context
        assert "RI" not in ctx.per_ticker
        # BR should have a forecast
        assert "BR" in ctx.per_ticker
        # Confidence should be 0.0 (dummy adapter)
        assert ctx.confidence_score == 0.0

    def test_prefilter_with_built_context(self):
        """Build context from fixture, then run prefilter — should fail-open (dummy=low conf)."""
        fixture_path = os.path.join(FIXTURES_DIR, "forecast_context_fixture.json")
        with open(fixture_path, "r", encoding="utf-8") as f:
            fixture = json.load(f)

        adapter = DummyTimesFMAdapter()
        ctx = build_forecast_context(
            adapter, fixture["bars_map"], fixture.get("regime_snapshot")
        )

        ideas = fixture.get("ideas", []) or [
            {"ticker": "BR", "strategy_name": "ema_cross", "direction": "LONG"},
            {"ticker": "GAZP", "strategy_name": "donchian", "direction": "SHORT"},
        ]
        config = fixture.get("config", {"excluded": ["RI"], "max_slots": 3})

        result = forecast_prefilter(ideas, ctx, config)
        # Dummy adapter → low confidence → fail-open
        assert result["meta"]["fail_open"] is True
        # All ideas should pass
        assert len(result["passed"]) == len(ideas)
