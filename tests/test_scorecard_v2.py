"""Tests: Scorecard V2 — fixture-based тесты для risk_allocator_scorecard_v2.

Закрывает desync #3 (statistics модули изолированы) + даёт PnL/risk scorecard.
Все тесты на mock-данных, без side-effects.

Покрытие:
  - test_composite_score_is_bounded: composite ∈ [0,1]
  - test_score_pool_excludes_ri: score_pool() исключает RI
  - test_score_pool_respects_max_slots: score_pool() ≤ max_slots
  - test_expectancy_drives_ranking: лучший expectancy → выше в ranking
  - test_risk_penalty_reduces_score: высокий drawdown снижает composite

Источник: plan.md Фича 5
"""
import sys
from pathlib import Path

import pytest

# Ensure imports
COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "code"))

from risk_allocator_scorecard_v2 import compute_composite, score_pool


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def good_candidate():
    """Кандидат с хорошими метриками."""
    return {
        "ticker": "LKOH",
        "direction": "LONG",
        "win_rate": 0.60,
        "avg_win": 200.0,
        "avg_loss": 100.0,
        "drawdown_pct": 3.0,
        "volatility": 1.5,
    }


@pytest.fixture
def bad_candidate():
    """Кандидат с плохими метриками."""
    return {
        "ticker": "GAZP",
        "direction": "SHORT",
        "win_rate": 0.35,
        "avg_win": 80.0,
        "avg_loss": 150.0,
        "drawdown_pct": 12.0,
        "volatility": 8.0,
    }


@pytest.fixture
def ri_candidate():
    """Кандидат RI (для теста исключения)."""
    return {
        "ticker": "RI",
        "direction": "LONG",
        "win_rate": 0.70,
        "avg_win": 500.0,
        "avg_loss": 200.0,
    }


@pytest.fixture
def mock_regime_snapshot():
    """Regime snapshot с trend для LKOH и range для GAZP."""
    return {
        "tickers": {
            "LKOH": {"adx": 35, "direction": "up", "regime": "trend"},
            "GAZP": {"adx": 10, "direction": "neutral", "regime": "range"},
        },
        "bias": "neutral",
    }


@pytest.fixture
def pool_with_diverse_strategies():
    """Pool с 5 стратегиями разных тикеров и quality."""
    return {
        "strategies": {
            "LKOH__vwap_reversion": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "rank_score": 5120.0,
                "status": "active",
                "direction": "LONG",
                "metrics": {"win_rate": 0.60, "avg_win": 200.0, "avg_loss": 100.0},
                "drawdown_pct": 3.0,
                "volatility": 1.5,
            },
            "GAZP__momentum": {
                "ticker": "GAZP",
                "strategy": "momentum",
                "rank_score": 4010.0,
                "status": "active",
                "direction": "SHORT",
                "metrics": {"win_rate": 0.35, "avg_win": 80.0, "avg_loss": 150.0},
                "drawdown_pct": 12.0,
                "volatility": 8.0,
            },
            "RI__nfi_trend": {
                "ticker": "RI",
                "strategy": "nfi_trend",
                "rank_score": 3500.0,
                "status": "active",
                "direction": "LONG",
                "metrics": {"win_rate": 0.70, "avg_win": 500.0, "avg_loss": 200.0},
            },
            "SBER__rsi_reversal": {
                "ticker": "SBER",
                "strategy": "rsi_reversal",
                "rank_score": 1600.0,
                "status": "active",
                "direction": "LONG",
                "metrics": {"win_rate": 0.50, "avg_win": 150.0, "avg_loss": 100.0},
                "drawdown_pct": 5.0,
            },
            "Si__breakout": {
                "ticker": "Si",
                "strategy": "breakout",
                "rank_score": 1200.0,
                "status": "active",
                "direction": "LONG",
                "metrics": {"win_rate": 0.45, "avg_win": 120.0, "avg_loss": 90.0},
            },
        }
    }


@pytest.fixture
def paper_config():
    """Config для dry-run."""
    return {
        "mode": "paper",
        "risk": {
            "max_slots": 3,
            "signal_min_rank": 100.0,
        },
        "excluded": ["RI"],
    }


# ─── Tests: compute_composite ────────────────────────────────────────

def test_composite_score_is_bounded(good_candidate, bad_candidate, mock_regime_snapshot):
    """composite_score ∈ [0, 1] для любого валидного кандидата."""
    for candidate in [good_candidate, bad_candidate]:
        result = compute_composite(candidate, regime_snapshot=mock_regime_snapshot)
        assert 0.0 <= result["composite_score"] <= 1.0, (
            f"composite_score={result['composite_score']} не в [0,1] "
            f"для {candidate['ticker']}"
        )

    # Проверяем что компоненты корректно разделены
    good = compute_composite(good_candidate, regime_snapshot=mock_regime_snapshot)
    bad = compute_composite(bad_candidate, regime_snapshot=mock_regime_snapshot)

    # У good lower risk_penalty (3% drawdown vs 12%)
    assert good["risk_penalty"] < bad["risk_penalty"]
    # У good higher expectancy
    assert good["expectancy_r"] > bad["expectancy_r"]


# ─── Tests: score_pool ───────────────────────────────────────────────

def test_score_pool_excludes_ri(pool_with_diverse_strategies, paper_config, mock_regime_snapshot):
    """score_pool() не включает RI в результат."""
    result = score_pool(
        pool_with_diverse_strategies, paper_config, mock_regime_snapshot
    )

    # RI не должен быть в результате
    tickers = [r["ticker"] for r in result]
    assert "RI" not in tickers, "RI должен быть исключён из score_pool"


def test_score_pool_respects_max_slots(pool_with_diverse_strategies, paper_config, mock_regime_snapshot):
    """score_pool() возвращает не более max_slots кандидатов."""
    result = score_pool(
        pool_with_diverse_strategies, paper_config, mock_regime_snapshot
    )

    assert len(result) <= paper_config["risk"]["max_slots"], (
        f"score_pool вернул {len(result)} кандидатов, "
        f"а max_slots={paper_config['risk']['max_slots']}"
    )


def test_expectancy_drives_ranking(mock_regime_snapshot):
    """Кандидат с лучшим expectancy_r ranked выше при прочих равных."""
    high_expectancy = {
        "ticker": "A",
        "direction": "LONG",
        "win_rate": 0.70,
        "avg_win": 300.0,
        "avg_loss": 100.0,
        "drawdown_pct": 5.0,
    }
    low_expectancy = {
        "ticker": "B",
        "direction": "LONG",
        "win_rate": 0.40,
        "avg_win": 100.0,
        "avg_loss": 100.0,
        "drawdown_pct": 5.0,
    }

    r_high = compute_composite(high_expectancy, regime_snapshot=mock_regime_snapshot)
    r_low = compute_composite(low_expectancy, regime_snapshot=mock_regime_snapshot)

    assert r_high["composite_score"] > r_low["composite_score"], (
        f"Higher expectancy ({r_high['expectancy_r']:.2f}) should yield "
        f"higher composite ({r_high['composite_score']:.4f}) than "
        f"lower expectancy ({r_low['expectancy_r']:.2f}, "
        f"composite={r_low['composite_score']:.4f})"
    )


def test_risk_penalty_reduces_score():
    """Высокий drawdown снижает composite score."""
    safe = {
        "ticker": "X",
        "direction": "LONG",
        "win_rate": 0.50,
        "avg_win": 150.0,
        "avg_loss": 100.0,
        "drawdown_pct": 2.0,
    }
    risky = {
        "ticker": "X",
        "direction": "LONG",
        "win_rate": 0.50,
        "avg_win": 150.0,
        "avg_loss": 100.0,
        "drawdown_pct": 14.0,
    }

    r_safe = compute_composite(safe)
    r_risky = compute_composite(risky)

    assert r_safe["composite_score"] > r_risky["composite_score"], (
        f"Safe candidate (dd={safe['drawdown_pct']}%) should have higher "
        f"composite than risky (dd={risky['drawdown_pct']}%)"
    )
    assert r_safe["risk_penalty"] < r_risky["risk_penalty"], (
        "Safe should have lower risk_penalty"
    )
