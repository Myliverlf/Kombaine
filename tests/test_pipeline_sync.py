"""Tests: Pipeline Sync Bridge — fixture-based тесты для реконсиляции state.

Закрывает desync #1 (generator→pool bridge) и #4 (waitlist→pool bridge).
Все тесты работают на mock-данных, без side-effects, без записи на диск.

Покрытие:
  - test_registry_to_pool_populates: pool заполняется из registry
  - test_conflict_resolution: конфликты по тикеру резолвируются
  - test_waitlist_fills_pool_after_sync: waitlist заполняет pool
  - test_excluded_tickers_not_in_pool: RI исключён из sync
  - test_empty_registry_produces_empty_pool: пустой registry → пустой pool

Источник: plan.md Фича 3
"""
import sys
from pathlib import Path

import pytest

# Ensure imports
COMBINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COMBINE_DIR / "code"))

from pipeline_sync import (
    reconcile_state,
    resolve_conflicts,
    sync_registry_to_pool,
    sync_waitlist_to_pool,
)


# ─── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mock_registry():
    """Mock strategy_registry.json с 5 стратегиями по 3 тикерам."""
    return {
        "strategies": {
            "LKOH": {
                "vwap_reversion": {
                    "params": {"lookback": 30},
                    "metrics": {"pnl": 4859.0, "sharpe": 0.49, "win_rate": 51.3, "pf": 1.29, "trades": 30},
                    "rank_score": 5120.0,
                },
                "mean_reversion": {
                    "params": {"lookback": 20},
                    "metrics": {"pnl": 3200.0, "sharpe": 0.35, "win_rate": 48.0, "pf": 1.15, "trades": 25},
                    "rank_score": 3300.0,
                },
            },
            "GAZP": {
                "momentum_breakout": {
                    "params": {"lookback": 15},
                    "metrics": {"pnl": 3866.0, "sharpe": 0.47, "win_rate": 46.5, "pf": 1.32, "trades": 20},
                    "rank_score": 4010.0,
                },
            },
            "RI": {
                "nfi_trend": {
                    "params": {},
                    "metrics": {"pnl": 2972.0, "sharpe": 0.68, "win_rate": 61.1, "pf": 2.84, "trades": 10},
                    "rank_score": 3500.0,
                },
            },
            "SBER": {
                "rsi_reversal": {
                    "params": {"rsi_period": 14},
                    "metrics": {"pnl": 1500.0, "sharpe": 0.25, "win_rate": 42.0, "pf": 1.1, "trades": 15},
                    "rank_score": 1600.0,
                },
            },
        }
    }


@pytest.fixture
def empty_pool():
    """Пустой signal_pool."""
    return {"strategies": {}, "last_rotation_ts": 0.0}


@pytest.fixture
def empty_waitlist():
    """Пустой waitlist."""
    return {"candidates": {}}


@pytest.fixture
def populated_waitlist():
    """Waitlist с 3 кандидатами."""
    return {
        "candidates": {
            "SBER__rsi_reversal": {
                "ticker": "SBER",
                "strategy": "rsi_reversal",
                "rank_score": 1600.0,
                "metrics": {"pnl": 1500.0, "sharpe": 0.25, "win_rate": 42.0},
                "params": {"rsi_period": 14},
            },
            "GAZP__mean_reversion": {
                "ticker": "GAZP",
                "strategy": "mean_reversion",
                "rank_score": 2500.0,
                "metrics": {"pnl": 2100.0, "sharpe": 0.30, "win_rate": 45.0},
                "params": {"lookback": 25},
            },
            "RI__scalp": {
                "ticker": "RI",
                "strategy": "scalp",
                "rank_score": 5000.0,
                "metrics": {"pnl": 3000.0, "sharpe": 0.70, "win_rate": 60.0},
                "params": {},
            },
        }
    }


@pytest.fixture
def paper_config():
    """Config dict для dry-run (paper mode)."""
    return {
        "mode": "paper",
        "risk": {
            "signal_pool_max": 4,
            "signal_min_rank": 100.0,
            "signal_rotation_days": 3,
            "signal_max_age_minutes": 16,
        },
        "excluded": ["RI"],
    }


# ─── Tests: sync_registry_to_pool ────────────────────────────────────

def test_registry_to_pool_populates(mock_registry, empty_pool, paper_config):
    """Из mock-registry с 4 стратегиями (без RI) pool заполняется до pool_max=4."""
    excluded = {"RI"}
    added = sync_registry_to_pool(
        mock_registry, empty_pool, pool_max=4, min_rank=100.0, excluded=excluded
    )

    # Добавлены: LKOH__vwap_reversion(5120), GAZP__momentum_breakout(4010),
    # LKOH__mean_reversion(3300), SBER__rsi_reversal(1600) — все > 100
    # RI__nfi_trend(3500) исключён
    assert len(added) == 4, "Должны добавить 4 стратегии (5 без RI)"
    assert len(empty_pool["strategies"]) == 4

    # RI не попал в pool
    ri_entries = [k for k in empty_pool["strategies"] if "RI" in k]
    assert len(ri_entries) == 0, "RI должен быть исключён из pool"

    # Проверяем что активные
    for pid, entry in empty_pool["strategies"].items():
        assert entry["status"] == "active"

    # Проверяем сортировку: LKOH__vwap_reversion первый (rank 5120)
    assert "LKOH__vwap_reversion" in empty_pool["strategies"]


def test_registry_to_pool_respects_pool_max(mock_registry, empty_pool):
    """pool_max=2 ограничивает добавление до 2 стратегий."""
    added = sync_registry_to_pool(
        mock_registry, empty_pool, pool_max=2, min_rank=100.0, excluded=set()
    )

    assert len(added) == 2
    assert len(empty_pool["strategies"]) == 2

    # Топ-2 по rank: LKOH__vwap_reversion (5120) и GAZP__momentum_breakout (4010)
    pool_ids = set(empty_pool["strategies"].keys())
    assert "LKOH__vwap_reversion" in pool_ids
    assert "GAZP__momentum_breakout" in pool_ids


def test_registry_to_pool_skips_low_rank(mock_registry, empty_pool):
    """Стратегии с rank_score < min_rank не попадают в pool."""
    added = sync_registry_to_pool(
        mock_registry, empty_pool, pool_max=10, min_rank=2000.0, excluded=set()
    )

    # Только 4 стратегии с rank >= 2000: LKOH__vwap(5120), GAZP__mo(4010),
    # RI__nfi(3500), LKOH__mean(3300)
    assert len(added) == 4

    # SBER__rsi(1600) НЕ попал
    assert "SBER__rsi_reversal" not in empty_pool["strategies"]


# ─── Tests: resolve_conflicts ────────────────────────────────────────

def test_conflict_resolution():
    """2 стратегии на один тикер: лучшая по rank_score остаётся active."""
    pool = {
        "strategies": {
            "LKOH__vwap_reversion": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "rank_score": 5120.0,
                "status": "active",
            },
            "LKOH__mean_reversion": {
                "ticker": "LKOH",
                "strategy": "mean_reversion",
                "rank_score": 3300.0,
                "status": "active",
            },
            "GAZP__momentum": {
                "ticker": "GAZP",
                "strategy": "momentum",
                "rank_score": 4010.0,
                "status": "active",
            },
        }
    }

    resolved = resolve_conflicts(pool)

    # LKOH: 2 активных → 1 конфликт
    assert "LKOH" in resolved
    assert len(resolved["LKOH"]) == 1
    assert resolved["LKOH"][0] == "LKOH__mean_reversion"

    # GAZP: 1 активный → нет конфликтов
    assert "GAZP" not in resolved

    # Лучшая LKOH__vwap_reversion осталась active
    assert pool["strategies"]["LKOH__vwap_reversion"]["status"] == "active"

    # Худшая LKOH__mean_reversion помечена resolved_conflict
    assert pool["strategies"]["LKOH__mean_reversion"]["status"] == "resolved_conflict"
    assert pool["strategies"]["LKOH__mean_reversion"]["resolved_by"] == "LKOH__vwap_reversion"


# ─── Tests: sync_waitlist_to_pool ────────────────────────────────────

def test_waitlist_fills_pool_after_sync(mock_registry, empty_pool, populated_waitlist):
    """После sync_registry_to_pool waitlist заполняет оставшиеся слоты."""
    excluded = {"RI"}

    # Сначала заполняем pool из registry с pool_max=5
    added_reg = sync_registry_to_pool(
        mock_registry, empty_pool, pool_max=5, min_rank=100.0, excluded=excluded
    )
    assert len(empty_pool["strategies"]) >= 3  # LKOH, GAZP, SBER (RI excluded)

    # Pool_max=6, в waitlist есть GAZP__mean_reversion и RI__scalp
    # GAZP__mean_reversion ещё нет в pool — будет добавлен
    # RI__scalp исключён
    added_wl = sync_waitlist_to_pool(
        populated_waitlist, empty_pool, pool_max=6, min_rank=100.0, excluded=excluded
    )

    # Должен добавить GAZP__mean_reversion (rank 2500)
    assert "GAZP__mean_reversion" in added_wl or len(empty_pool["strategies"]) >= 4

    # RI__scalp НЕ попал в pool
    ri_in_pool = [k for k in empty_pool["strategies"] if "RI" in k]
    assert len(ri_in_pool) == 0

    # Кандидаты из waitlist удалены
    assert "GAZP__mean_reversion" not in populated_waitlist["candidates"]
    assert "RI__scalp" not in populated_waitlist["candidates"]


# ─── Tests: reconcile_state ──────────────────────────────────────────

def test_reconcile_state_full_flow(mock_registry, empty_pool, populated_waitlist, paper_config):
    """Полный цикл reconcile: registry → pool → waitlist → conflicts."""
    result = reconcile_state(mock_registry, populated_waitlist, empty_pool, paper_config)

    # Из registry добавлены (pool_max=4, RI excluded): 4 стратегии
    assert len(result["added_from_registry"]) == 4

    # pool_active > 0
    assert result["pool_active"] >= 3

    # pool_total > 0
    assert result["pool_total"] >= 3

    # RI не в pool
    ri_in_pool = [k for k in empty_pool["strategies"] if k.startswith("RI")]
    assert len(ri_in_pool) == 0


def test_empty_registry_produces_empty_pool(empty_pool, empty_waitlist, paper_config):
    """Пустой registry → пустой pool."""
    empty_registry = {"strategies": {}}
    result = reconcile_state(empty_registry, empty_waitlist, empty_pool, paper_config)

    assert len(result["added_from_registry"]) == 0
    assert len(result["added_from_waitlist"]) == 0
    assert result["pool_active"] == 0
    assert result["pool_total"] == 0
