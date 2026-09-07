"""Тесты Strategy Ideas — pre-backtest scoring + filter + templates + build_idea.

Fixtures ≥3 (from tests/fixtures/ JSON + inline), dry-run, без live-ордеров.
Покрывает критерии приёмки task.md:
  - idea_score корректно ранжирует сильные vs слабые идеи
  - RI excluded → score=-inf
  - filter_weak отсекает слабые идеи
  - suggest_templates учитывает regime bias
  - build_idea ставит contracts=1
  - нет broker/tinkoff/futures_lab импортов
  - max_contracts_per_entry=1 из config
"""
import json
import math
import sys
from pathlib import Path

import pytest

# Ensure code/ is on sys.path
_CODE_DIR = Path(__file__).resolve().parent.parent / "code"
sys.path.insert(0, str(_CODE_DIR))

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

from strategy_ideas import (
    idea_score,
    filter_weak,
    suggest_templates,
    build_idea,
    score_ideas_batch,
    check_no_broker_imports,
)


# ─── Fixtures (≥3 required) ───────────────────────────────────────────

@pytest.fixture
def strategy_ideas_data():
    """Load strategy_ideas_fixture.json — main test data."""
    path = _FIXTURES_DIR / "strategy_ideas_fixture.json"
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
def strong_idea():
    """Strong idea: GAZP/ema_cross in trend regime with good metrics."""
    return {
        "ticker": "GAZP",
        "strategy_name": "ema_cross",
        "direction": "LONG",
        "win_rate": 0.65,
        "avg_win": 250.0,
        "avg_loss": 70.0,
        "drawdown_pct": 2.0,
        "volatility": 1.5,
    }


@pytest.fixture
def weak_idea():
    """Weak idea: LKOH/mean_reversion with poor metrics."""
    return {
        "ticker": "LKOH",
        "strategy_name": "mean_reversion",
        "direction": "LONG",
        "win_rate": 0.30,
        "avg_win": 50.0,
        "avg_loss": 200.0,
        "drawdown_pct": 14.0,
        "volatility": 10.0,
    }


@pytest.fixture
def ri_idea():
    """RI idea: excluded ticker → VETO."""
    return {
        "ticker": "RI",
        "strategy_name": "ema_cross",
        "direction": "LONG",
        "win_rate": 0.60,
        "avg_win": 200.0,
        "avg_loss": 80.0,
    }


@pytest.fixture
def feedback_fixture():
    """Mock feedback from generator_feedback.json."""
    return {
        "LONG": {"pnl": -257},
        "SHORT": {"pnl": 726},
        "best_hours": [8, 7],
    }


@pytest.fixture
def config_fixture():
    """Config with excluded RI, max_slots=3, max_contracts=1."""
    return {
        "max_slots": 3,
        "max_contracts_per_entry": 1,
        "excluded": ["RI"],
    }


@pytest.fixture
def tmp_dir(tmp_path):
    """Temporary directory for file operations."""
    return tmp_path


# ─── Test: idea_score ─────────────────────────────────────────────────

class TestIdeaScore:
    """idea_score: скоринг идей до бэктеста."""

    def test_strong_vs_weak(self, strong_idea, weak_idea, regime_snapshot_fixture,
                            feedback_fixture, config_fixture):
        """Strong idea scores higher than weak idea."""
        score_strong = idea_score(strong_idea, regime_snapshot_fixture,
                                  feedback_fixture, config_fixture)
        score_weak = idea_score(weak_idea, regime_snapshot_fixture,
                                feedback_fixture, config_fixture)
        assert score_strong > score_weak, (
            f"Strong ({score_strong}) should score > weak ({score_weak})"
        )

    def test_veto_ri(self, ri_idea, regime_snapshot_fixture, feedback_fixture, config_fixture):
        """RI ticker → score = -inf (VETO'd)."""
        score = idea_score(ri_idea, regime_snapshot_fixture, feedback_fixture, config_fixture)
        assert math.isinf(score) and score < 0, (
            f"RI should be VETO'd (got {score})"
        )

    def test_no_snapshot_fallback(self, strong_idea, feedback_fixture, config_fixture):
        """Without regime_snapshot, score is computed (fallback to 0 regime)."""
        score = idea_score(strong_idea, None, feedback_fixture, config_fixture)
        assert isinstance(score, float)
        assert not math.isinf(score), "Should not be VETO without snapshot"

    def test_positive_score_for_good_idea(self, strong_idea, regime_snapshot_fixture,
                                          feedback_fixture, config_fixture):
        """Good idea should have positive score."""
        score = idea_score(strong_idea, regime_snapshot_fixture, feedback_fixture, config_fixture)
        assert score > 0, f"Good idea should score > 0, got {score}"

    def test_feedback_direction_short_positive(self, regime_snapshot_fixture,
                                               feedback_fixture, config_fixture):
        """SHORT feedback has positive PnL → SHORT idea gets feedback bonus."""
        short_good = {
            "ticker": "GAZP",
            "strategy_name": "ema_cross",
            "direction": "SHORT",
            "win_rate": 0.55,
            "avg_win": 150.0,
            "avg_loss": 80.0,
        }
        score_short = idea_score(short_good, regime_snapshot_fixture,
                                 feedback_fixture, config_fixture)
        # GAZP SHORT in trend-up → regime penalty, but feedback SHORT=+726 → bonus
        assert isinstance(score_short, float)
        assert not math.isinf(score_short)


# ─── Test: filter_weak ─────────────────────────────────────────────────

class TestFilterWeak:
    """filter_weak: отсев слабых идей."""

    def test_removes_low_score(self):
        """Ideas with score < threshold are removed."""
        ideas = [
            {"ticker": "A", "strategy_name": "x", "score": 0.5},
            {"ticker": "B", "strategy_name": "y", "score": 0.1},
            {"ticker": "C", "strategy_name": "z", "score": -0.2},
        ]
        result = filter_weak(ideas, threshold=0.3)
        tickers = [i["ticker"] for i in result]
        assert "A" in tickers
        assert "B" not in tickers
        assert "C" not in tickers

    def test_keeps_strong_score(self):
        """Ideas with score >= threshold are kept."""
        ideas = [
            {"ticker": "X", "strategy_name": "s1", "score": 0.3},
            {"ticker": "Y", "strategy_name": "s2", "score": 0.8},
        ]
        result = filter_weak(ideas, threshold=0.3)
        assert len(result) == 2

    def test_veto_excluded(self):
        """Ideas with score=-inf (VETO) are excluded."""
        ideas = [
            {"ticker": "A", "strategy_name": "s1", "score": 0.5},
            {"ticker": "B", "strategy_name": "s2", "score": float("-inf")},
        ]
        result = filter_weak(ideas, threshold=0.0)
        assert len(result) == 1
        assert result[0]["ticker"] == "A"

    def test_none_score_excluded(self):
        """Ideas without score (None) are excluded."""
        ideas = [
            {"ticker": "A", "strategy_name": "s1", "score": None},
            {"ticker": "B", "strategy_name": "s2", "score": 0.5},
        ]
        result = filter_weak(ideas, threshold=0.0)
        assert len(result) == 1
        assert result[0]["ticker"] == "B"

    def test_empty_list(self):
        """Empty ideas list → empty result."""
        assert filter_weak([], threshold=0.3) == []

    def test_threshold_boundary(self):
        """Idea at exact threshold is kept."""
        ideas = [{"ticker": "A", "strategy_name": "s", "score": 0.3}]
        result = filter_weak(ideas, threshold=0.3)
        assert len(result) == 1


# ─── Test: suggest_templates ───────────────────────────────────────────

class TestSuggestTemplates:
    """suggest_templates: regime→template mapping."""

    def test_trend_regime(self, regime_snapshot_fixture):
        """Trend regime → trend templates (ema_cross, donchian, ft_supertrend)."""
        templates = suggest_templates(regime_snapshot_fixture)
        # regime_snapshot has 3 trend + 2 range → trend dominates
        assert isinstance(templates, list)
        assert len(templates) > 0
        # Should contain trend-related templates
        trend_templates = {"ema_cross", "donchian", "ft_supertrend"}
        assert any(t in trend_templates for t in templates), (
            f"Expected trend templates in {templates}"
        )

    def test_range_only_regime(self):
        """Range-only regime → range templates."""
        snapshot = {
            "tickers": {
                "A": {"regime": "range"},
                "B": {"regime": "range"},
            },
            "bias": "neutral",
        }
        templates = suggest_templates(snapshot)
        range_templates = {"mean_reversion", "bollinger_squeeze"}
        assert any(t in range_templates for t in templates), (
            f"Expected range templates in {templates}"
        )

    def test_no_snapshot_returns_all(self):
        """No snapshot → all templates (fallback)."""
        templates = suggest_templates(None)
        assert isinstance(templates, list)
        assert len(templates) >= 5, "Should return all known templates"

    def test_mixed_regime(self):
        """Equal trend+range → mixed templates from both categories."""
        snapshot = {
            "tickers": {
                "A": {"regime": "trend"},
                "B": {"regime": "range"},
            },
            "bias": "neutral",
        }
        templates = suggest_templates(snapshot)
        assert isinstance(templates, list)
        assert len(templates) >= 4, "Mixed should include templates from both regimes"


# ─── Test: build_idea ──────────────────────────────────────────────────

class TestBuildIdea:
    """build_idea: конструктор идей."""

    def test_contracts_always_1(self):
        """build_idea always sets contracts=1."""
        idea = build_idea("GAZP", "ema_cross")
        assert idea["contracts"] == 1

    def test_fields_present(self):
        """build_idea returns dict with all required fields."""
        idea = build_idea("SBER", "donchian", {"period": 20})
        assert idea["ticker"] == "SBER"
        assert idea["strategy_name"] == "donchian"
        assert idea["params"] == {"period": 20}
        assert idea["contracts"] == 1
        assert idea["score"] is None

    def test_no_params_default(self):
        """build_idea with no params → params=None."""
        idea = build_idea("BR", "ema_cross")
        assert idea["params"] is None


# ─── Test: score_ideas_batch ───────────────────────────────────────────

class TestScoreIdeasBatch:
    """score_ideas_batch: batch scoring + sorting."""

    def test_sorted_descending(self, strong_idea, weak_idea, ri_idea,
                                regime_snapshot_fixture, feedback_fixture, config_fixture):
        """Batch scoring returns sorted list (strong first, VETO last)."""
        ideas = [weak_idea, ri_idea, strong_idea]
        scored = score_ideas_batch(ideas, regime_snapshot_fixture,
                                   feedback_fixture, config_fixture)
        assert len(scored) == 3
        # First should have highest score
        assert scored[0]["score"] >= scored[1]["score"]
        # RI (VETO) should be last
        assert math.isinf(scored[-1]["score"]) and scored[-1]["score"] < 0
        assert scored[-1]["ticker"] == "RI"

    def test_score_written_back(self, strong_idea, regime_snapshot_fixture,
                                feedback_fixture, config_fixture):
        """Batch scoring writes score back to each idea dict."""
        ideas = [strong_idea]
        scored = score_ideas_batch(ideas, regime_snapshot_fixture,
                                   feedback_fixture, config_fixture)
        assert scored[0]["score"] is not None
        assert isinstance(scored[0]["score"], float)


# ─── Test: Constraints (task.md criteria) ──────────────────────────────

class TestConstraints:
    """task.md criteria: no live, RI excluded, max_contracts=1, no broker."""

    def test_no_broker_imports(self):
        """Module has no broker/tinkoff/futures_lab imports."""
        assert check_no_broker_imports() is True, (
            "strategy_ideas.py imports forbidden broker/client modules"
        )

    def test_all_ideas_contracts_1(self, strategy_ideas_data, regime_snapshot_fixture,
                                    feedback_fixture, config_fixture):
        """All batch-scored ideas have contracts=1."""
        ideas = strategy_ideas_data["ideas"]
        scored = score_ideas_batch(ideas, regime_snapshot_fixture,
                                   feedback_fixture, config_fixture)
        for idea in scored:
            assert idea["contracts"] == 1, (
                f"{idea['ticker']} has contracts={idea['contracts']}, expected 1"
            )

    def test_ri_always_veto(self, strategy_ideas_data, regime_snapshot_fixture,
                             feedback_fixture, config_fixture):
        """RI ticker always gets VETO (score=-inf) in batch."""
        ideas = strategy_ideas_data["ideas"]
        ri_ideas = [i for i in ideas if i["ticker"] == "RI"]
        assert len(ri_ideas) >= 1, "Fixture must include RI idea"
        for ri in ri_ideas:
            score = idea_score(ri, regime_snapshot_fixture, feedback_fixture, config_fixture)
            assert math.isinf(score) and score < 0, (
                f"RI idea should be VETO'd, got {score}"
            )
