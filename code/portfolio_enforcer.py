"""Enforce max_slots в portfolio.json: дедупликация + обрезка до max_slots.

Факт: state/portfolio.json содержит 8 слотов (GAZP×5, LKOH×3) вместо ≤3.
config.json задаёт risk.max_slots=3. Enforcement отсутствует на write-path.

Алгоритм:
1. Дедупликация: при дублях (ticker, strategy) оставляем слот с лучшим pnl_rub.
2. Сортировка по (has_open_position DESC, pnl_rub DESC, promoted_ts ASC).
3. Обрезка до max_slots.
4. Запись обратно в portfolio.json.

Возвращает (before_count, after_count, removed_ids).

Источник: analysis.md (fact #9, R2), config.json risk.max_slots=3.
"""
import json
import shutil
import time
from pathlib import Path
from typing import Optional

COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
STATE_DIR = COMBINE_DIR / "state"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"


def _slot_sort_key(slot: dict) -> tuple:
    """Сортировка слотов: приоритет активным позициям, затем по.pnl_rub."""
    has_position = 1 if slot.get("open_position") else 0
    pnl = slot.get("pnl_rub", 0.0)
    promoted = slot.get("promoted_ts", float("inf"))
    return (-has_position, -pnl, promoted)


def enforce_portfolio(
    portfolio_path: str | Path = PORTFOLIO_FILE,
    max_slots: int = 3,
) -> tuple[int, int, list[str]]:
    """Дедуплицирует слоты по (ticker, strategy) и обрезает до max_slots.

    Args:
        portfolio_path: путь к portfolio.json
        max_slots: максимальное количество слотов

    Returns:
        (before_count, after_count, removed_slot_ids)
    """
    portfolio_path = Path(portfolio_path)
    if not portfolio_path.exists():
        return (0, 0, [])

    portfolio = json.loads(portfolio_path.read_text())
    slots = portfolio.get("slots", {})
    before_count = len(slots)

    # ── Шаг 1: Дедупликация по (ticker, strategy) ──────────────────
    # Дедуп выполняется ВСЕГДА, даже если slots <= max_slots.
    # Максимальный лимит проверяется отдельно (truncate).
    deduped: dict[str, dict] = {}
    for slot_id, slot in slots.items():
        key = (slot.get("ticker", ""), slot.get("strategy", ""))
        if key not in deduped:
            deduped[key] = (slot_id, slot)
        else:
            existing_id, existing_slot = deduped[key]
            # Оставляем лучший по pnl_rub (с учётом open_position приоритета)
            new_score = _slot_sort_key(slot)
            old_score = _slot_sort_key(existing_slot)
            if new_score < old_score:  # mnievie = лучше (отрицательные в sortOrder)
                deduped[key] = (slot_id, slot)

    # ── Шаг 2: Сортировка + обрезка до max_slots ──────────────────
    unique_slots = [(sid, s) for sid, s in deduped.values()]
    unique_slots.sort(key=lambda x: _slot_sort_key(x[1]))

    kept = unique_slots[:max_slots]
    removed_from_dedup = unique_slots[max_slots:]

    # Собираем итоговый словарь
    new_slots = {}
    removed_ids = []
    for slot_id, slot in kept:
        new_slots[slot_id] = slot

    # Удаляем дубли, не попавшие в deduped
    for slot_id in slots:
        if slot_id not in {sid for sid, _ in kept}:
            removed_ids.append(slot_id)

    for slot_id, _ in removed_from_dedup:
        if slot_id not in removed_ids:
            removed_ids.append(slot_id)

    after_count = len(new_slots)

    # ── Шаг 3: Запись ──────────────────────────────────────────────
    if after_count < before_count:
        portfolio["slots"] = new_slots
        tmp = portfolio_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(portfolio, indent=2, ensure_ascii=False))
        tmp.replace(portfolio_path)

    return (before_count, after_count, removed_ids)


def get_portfolio_stats(portfolio_path: str | Path = PORTFOLIO_FILE) -> dict:
    """Возвращает статистику portfolio для scorecard."""
    portfolio_path = Path(portfolio_path)
    if not portfolio_path.exists():
        return {"exists": False, "n_slots": 0}

    portfolio = json.loads(portfolio_path.read_text())
    slots = portfolio.get("slots", {})
    tickers = set()
    strategies = set()
    total_pnl = 0.0
    active_positions = 0
    for s in slots.values():
        tickers.add(s.get("ticker", ""))
        strategies.add(s.get("strategy", ""))
        total_pnl += s.get("pnl_rub", 0.0)
        if s.get("open_position"):
            active_positions += 1

    return {
        "exists": True,
        "n_slots": len(slots),
        "unique_tickers": len(tickers),
        "unique_strategies": len(strategies),
        "total_pnl_rub": round(total_pnl, 2),
        "active_positions": active_positions,
        "tickers": sorted(tickers),
        "strategies": sorted(strategies),
    }


# ── Самотест ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Portfolio Enforcer Dry-Run ===")
    stats = get_portfolio_stats()
    print("Before: %d slots" % stats["n_slots"])
    print("Tickers: %s" % stats["tickers"])
    print("Strategies: %s" % stats["strategies"])
    print("Total PnL: %.2f RUB" % stats["total_pnl_rub"])
    print("Active positions: %d" % stats["active_positions"])

    # Dry-run: не пишем, только считаем
    portfolio_path = PORTFOLIO_FILE
    if portfolio_path.exists():
        portfolio = json.loads(portfolio_path.read_text())
        slots = portfolio.get("slots", {})

        deduped = {}
        for sid, s in slots.items():
            key = (s.get("ticker", ""), s.get("strategy", ""))
            if key not in deduped:
                deduped[key] = (sid, s)
            else:
                _, old = deduped[key]
                if _slot_sort_key(s) < _slot_sort_key(old):
                    deduped[key] = (sid, s)

        unique = [(sid, s) for sid, s in deduped.values()]
        unique.sort(key=lambda x: _slot_sort_key(x[1]))
        kept = [sid for sid, _ in unique[:3]]

        print("\nDry-run enforce: %d → %d slots" % (len(slots), len(kept)))
        print("Kept: %s" % kept)
    else:
        print("\nNo portfolio.json found")
