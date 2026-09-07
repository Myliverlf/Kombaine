"""Pipeline Sync Bridge: чистые функции для реконсиляции state между ветками pipeline.

Закрывает desync #1 (generator→pool bridge) и #4 (waitlist→pool bridge):
  - registry (28 strategies) → signal_pool (0 strategies) — pool пуст
  - waitlist (0) → signal_pool — bridge не работает

Все функции — dict-in/dict-out, без side-effects, без записи на диск.
Единственная точка входа: reconcile_state().

Источники:
  - core/registry.py: add_to_signal_pool, best_candidate, signal_conflicts
  - daily_generator.py: fill_from_waitlist, resolve_conflicts (логика, не импорт)
  - plan.md Фича 1
"""
from typing import Any, Dict, List, Optional, Set, Tuple


# ─── Внутренние утилиты ──────────────────────────────────────────────

def _is_excluded(ticker: str, excluded: Set[str]) -> bool:
    """Проверка: ticker в списке исключений (напр. RI)."""
    return ticker.upper() in excluded


def _strategy_entry_to_pool_format(
    ticker: str,
    strategy: str,
    strategy_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Конвертация записи из strategy_registry.json в формат signal_pool."""
    metrics = strategy_data.get("metrics", {})
    return {
        "ticker": ticker,
        "strategy": strategy,
        "params": strategy_data.get("params", {}),
        "metrics": {
            "pnl": metrics.get("pnl", 0.0),
            "sharpe": metrics.get("sharpe", 0.0),
            "win_rate": metrics.get("win_rate", 0.0),
            "pf": metrics.get("pf", 0.0),
            "trades": metrics.get("trades", 0),
            "trades_per_day": metrics.get("trades_per_day", 0.0),
        },
        "rank_score": strategy_data.get("rank_score", 0.0),
        "go_rub": strategy_data.get("go_rub", 2000.0),
        "status": "active",
    }


# ─── Фича 1.1: registry → pool ───────────────────────────────────────

def sync_registry_to_pool(
    registry_data: Dict[str, Any],
    pool: Dict[str, Any],
    pool_max: int = 10,
    min_rank: float = 100.0,
    excluded: Optional[Set[str]] = None,
) -> List[str]:
    """Заполняет signal_pool из strategy_registry.

    Берёт стратегии из registry, сортирует по rank_score DESC,
    исключает RI и стратегии ниже min_rank, добавляет до pool_max.

    Args:
        registry_data: contents of state/strategy_registry.json
        pool: signal_pool dict (modified in-place)
        pool_max: максимальный размер pool (default 10)
        min_rank: минимальный rank_score для включения (default 100)
        excluded: множество тикеров для исключения (напр. {"RI"})

    Returns:
        Список pool_id добавленных стратегий.
    """
    if excluded is None:
        excluded = set()

    strategies = registry_data.get("strategies", {})
    pool_strategies = pool.get("strategies", {})

    # Текущее количество активных в pool
    pool_active = sum(
        1 for p in pool_strategies.values() if p.get("status") == "active"
    )

    # Собираем кандидатов: (rank_score, ticker, strategy, data)
    candidates = []
    for ticker, ticker_strategies in strategies.items():
        if _is_excluded(ticker, excluded):
            continue
        for strategy_name, sdata in ticker_strategies.items():
            rank = sdata.get("rank_score", 0.0)
            if rank < min_rank:
                continue
            candidates.append((rank, ticker, strategy_name, sdata))

    # Сортируем по rank_score DESC
    candidates.sort(key=lambda x: x[0], reverse=True)

    added: List[str] = []
    for rank, ticker, strategy_name, sdata in candidates:
        if pool_active >= pool_max:
            break

        pool_id = "%s__%s" % (ticker, strategy_name)

        # Пропускаем если уже в pool и активна
        if pool_id in pool_strategies:
            existing = pool_strategies[pool_id]
            if existing.get("status") == "active":
                # Обновляем metrics если rank выше
                if rank > existing.get("rank_score", 0):
                    existing["metrics"] = sdata.get("metrics", {})
                    existing["rank_score"] = rank
                continue
            # Если статус не active — пересоздаём
            existing.update(_strategy_entry_to_pool_format(ticker, strategy_name, sdata))
            pool_active += 1
            added.append(pool_id)
            continue

        # Новая стратегия
        pool_strategies[pool_id] = _strategy_entry_to_pool_format(
            ticker, strategy_name, sdata
        )
        pool_active += 1
        added.append(pool_id)

    return added


# ─── Фича 1.2: waitlist → pool ───────────────────────────────────────

def sync_waitlist_to_pool(
    waitlist: Dict[str, Any],
    pool: Dict[str, Any],
    pool_max: int = 10,
    min_rank: float = 100.0,
    excluded: Optional[Set[str]] = None,
) -> List[str]:
    """Переносит лучших кандидатов из waitlist в pool.

    Args:
        waitlist: contents of state/waitlist.json
        pool: signal_pool dict (modified in-place)
        pool_max: максимальный размер pool
        min_rank: минимальный rank_score
        excluded: множество тикеров для исключения

    Returns:
        Список pool_id добавленных стратегий.
    """
    if excluded is None:
        excluded = set()

    candidates = waitlist.get("candidates", {})
    pool_strategies = pool.get("strategies", {})

    pool_active = sum(
        1 for p in pool_strategies.values() if p.get("status") == "active"
    )

    # Сортируем кандидатов по rank_score DESC
    sorted_cands = sorted(
        candidates.items(),
        key=lambda kv: kv[1].get("rank_score", 0.0),
        reverse=True,
    )

    added: List[str] = []
    to_remove: List[str] = []

    for cand_id, cand in sorted_cands:
        if pool_active >= pool_max:
            break

        ticker = cand.get("ticker", "")
        if _is_excluded(ticker, excluded):
            to_remove.append(cand_id)
            continue

        rank = cand.get("rank_score", 0.0)
        if rank < min_rank:
            continue

        strategy_name = cand.get("strategy", "")
        pool_id = "%s__%s" % (ticker, strategy_name)

        if pool_id in pool_strategies:
            existing = pool_strategies[pool_id]
            if existing.get("status") in ("active", "promoted"):
                # Уже в pool — обновляем если rank выше
                if rank > existing.get("rank_score", 0):
                    existing["rank_score"] = rank
                    existing["metrics"] = cand.get("metrics", {})
                to_remove.append(cand_id)
                continue
            # Пересоздаём если статус не active
            existing.update(_strategy_entry_to_pool_format(ticker, strategy_name, cand))
            pool_active += 1
            added.append(pool_id)
            to_remove.append(cand_id)
            continue

        pool_strategies[pool_id] = _strategy_entry_to_pool_format(
            ticker, strategy_name, cand
        )
        pool_active += 1
        added.append(pool_id)
        to_remove.append(cand_id)

    # Удаляем перенесённые кандидаты из waitlist
    for cid in to_remove:
        candidates.pop(cid, None)

    return added


# ─── Фича 1.3: конфликты ─────────────────────────────────────────────

def resolve_conflicts(
    signal_pool: Dict[str, Any],
) -> Dict[str, List[str]]:
    """Резолюция конфликтов: несколько стратегий на один тикер.

    Лучший по rank_score остаётся active, остальные помечаются resolved_conflict.

    Returns:
        Dict {ticker: [resolved_pool_ids]} — список разрешённых конфликтов.
    """
    pool_strategies = signal_pool.get("strategies", {})

    # Группируем по тикерам: ticker → [(pool_id, rank_score, entry)]
    by_ticker: Dict[str, List[Tuple[str, float, Dict[str, Any]]]] = {}
    for pid, entry in pool_strategies.items():
        if entry.get("status") != "active":
            continue
        ticker = entry.get("ticker", "")
        if ticker not in by_ticker:
            by_ticker[ticker] = []
        by_ticker[ticker].append((pid, entry.get("rank_score", 0.0), entry))

    resolved: Dict[str, List[str]] = {}
    for ticker, entries in by_ticker.items():
        if len(entries) <= 1:
            continue
        # Сортируем по rank_score DESC
        entries.sort(key=lambda x: x[1], reverse=True)
        best_pid = entries[0][0]
        ticker_resolved = []
        for pid, _rank, entry in entries[1:]:
            entry["status"] = "resolved_conflict"
            entry["resolved_by"] = best_pid
            ticker_resolved.append(pid)
        resolved[ticker] = ticker_resolved

    return resolved


# ─── Фича 1.4: единая точка входа ─────────────────────────────────────

def reconcile_state(
    registry: Dict[str, Any],
    waitlist: Dict[str, Any],
    pool: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Единая точка входа для реконсиляции state.

    Выполняет последовательно:
      1. sync_registry_to_pool — pool из registry
      2. sync_waitlist_to_pool — pool из waitlist (если есть место)
      3. resolve_conflicts — резолюция конфликтов по тикерам

    Args:
        registry: contents of state/strategy_registry.json
        waitlist: contents of state/waitlist.json (modified in-place)
        pool: signal_pool dict (modified in-place)
        config: config dict с risk params

    Returns:
        Dict с отчётом о реконсиляции:
        {
            "added_from_registry": [...],
            "added_from_waitlist": [...],
            "conflicts_resolved": {"ticker": [...]},
            "pool_active": int,
            "pool_total": int,
        }
    """
    risk = config.get("risk", {})
    excluded = set(config.get("excluded", []))
    pool_max = risk.get("signal_pool_max", 10)
    min_rank = risk.get("signal_min_rank", 100.0)

    # 1. Registry → Pool
    added_reg = sync_registry_to_pool(
        registry, pool, pool_max=pool_max, min_rank=min_rank, excluded=excluded
    )

    # 2. Waitlist → Pool (если есть место)
    added_wl = sync_waitlist_to_pool(
        waitlist, pool, pool_max=pool_max, min_rank=min_rank, excluded=excluded
    )

    # 3. Resolve conflicts
    conflicts = resolve_conflicts(pool)

    # Метрики
    pool_strategies = pool.get("strategies", {})
    pool_active = sum(1 for p in pool_strategies.values() if p.get("status") == "active")

    return {
        "added_from_registry": added_reg,
        "added_from_waitlist": added_wl,
        "conflicts_resolved": conflicts,
        "pool_active": pool_active,
        "pool_total": len(pool_strategies),
    }
