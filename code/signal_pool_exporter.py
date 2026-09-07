"""Экспорт signal_pool.json из registry state.

Факт: state/signal_pool.json = {"strategies": {}} — пустой.
Причина: export_legacy_state_files() не вызывается из pipeline flow.

Решение: standalone функция, которая:
1. Загружает registry state (StrategyRegistry из code/strategy_registry.py).
2. Фильтрует записи со статусом active_signal_pool или active_watchlist.
3. Записывает в signal_pool.json формат {strategies: {id: {...}}}.

Источник: analysis.md (fact #8, R4), strategy_registry.py:380-427.
"""
import json
import time
from pathlib import Path
from typing import Optional

COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
STATE_DIR = COMBINE_DIR / "state"
SIGNAL_POOL_FILE = STATE_DIR / "signal_pool.json"


def export_signal_pool(
    pool_path: str | Path = SIGNAL_POOL_FILE,
    state_dir: str | Path = STATE_DIR,
) -> int:
    """Экспортирует signal_pool.json из registry. Возвращает число записей.

    Использует StrategyRegistry для чтения записей со статусами:
    - active_signal_pool
    - active_watchlist
    (аналог export_legacy_signal_pool из code/strategy_registry.py:380)
    """
    try:
        from core.registry import load_registry_state, STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST
    except ImportError:
        # Fallback: читаем signal_pool напрямую из registry json
        return _export_from_registry_json(pool_path, state_dir)

    registry = load_registry_state()
    strategies = {}

    for record in registry.records():
        if record.status not in {STATUS_ACTIVE_SIGNAL_POOL, STATUS_ACTIVE_WATCHLIST}:
            continue
        strategies[record.strategy_id] = {
            "ticker": record.ticker,
            "strategy": record.strategy,
            "params": dict(record.params),
            "metrics": dict(record.metrics),
            "rank_score": float(record.active_rank or record.metrics.get("rank_score", 0.0)),
            "go_rub": float(record.portfolio_context.get("go_rub", 0.0)),
            "added_ts": record.created_ts,
            "last_signal_ts": record.updated_ts,
            "status": record.status,
        }

    pool_data = {
        "strategies": strategies,
        "last_rotation_ts": time.time(),
    }

    pool_path = Path(pool_path)
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pool_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pool_data, indent=2, ensure_ascii=False))
    tmp.replace(pool_path)

    return len(strategies)


def _export_from_registry_json(
    pool_path: str | Path,
    state_dir: str | Path,
) -> int:
    """Fallback: экспортирует signal_pool из registry.json напрямую."""
    registry_file = Path(state_dir) / "registry.json"
    if not registry_file.exists():
        # Пишем пустой пул
        pool_path = Path(pool_path)
        pool_data = {"strategies": {}, "last_rotation_ts": time.time()}
        tmp = pool_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pool_data, indent=2, ensure_ascii=False))
        tmp.replace(pool_path)
        return 0

    registry_data = json.loads(registry_file.read_text())
    strategies = {}

    for record_id, record in registry_data.get("records", {}).items():
        status = record.get("status", "")
        if status not in {"active_signal_pool", "active_watchlist"}:
            continue
        strategies[record_id] = {
            "ticker": record.get("ticker", ""),
            "strategy": record.get("strategy", ""),
            "params": record.get("params", {}),
            "metrics": record.get("metrics", {}),
            "rank_score": float(record.get("active_rank", 0.0) or 0.0),
            "go_rub": float(record.get("portfolio_context", {}).get("go_rub", 0.0)),
            "added_ts": record.get("created_ts", 0.0),
            "last_signal_ts": record.get("updated_ts", 0.0),
            "status": status,
        }

    pool_data = {
        "strategies": strategies,
        "last_rotation_ts": time.time(),
    }

    pool_path = Path(pool_path)
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pool_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pool_data, indent=2, ensure_ascii=False))
    tmp.replace(pool_path)

    return len(strategies)


def read_signal_pool(pool_path: str | Path = SIGNAL_POOL_FILE) -> dict:
    """Читает текущий signal_pool.json. Возвращает dict strategies."""
    pool_path = Path(pool_path)
    if not pool_path.exists():
        return {}
    data = json.loads(pool_path.read_text())
    return data.get("strategies", {})


# ── Самотест ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(COMBINE_DIR))

    print("=== Signal Pool Exporter Dry-Run ===")
    n = export_signal_pool()
    print("Exported %d strategies to %s" % (n, SIGNAL_POOL_FILE))

    if n > 0:
        pool = read_signal_pool()
        for pid, info in pool.items():
            print("  %s: ticker=%s strategy=%s score=%.0f" % (
                pid, info.get("ticker"), info.get("strategy"),
                info.get("rank_score", 0.0)))
    else:
        print("No active strategies in registry (empty pool expected)")
