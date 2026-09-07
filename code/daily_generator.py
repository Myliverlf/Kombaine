#!/usr/bin/env python3
"""Daily Generator: ежедневное пополнение, ротация и резолюция конфликтов signal pool.

Заменяет одноразовый seeder.py для повседневной работы.
Не требует Tinkoff API — работает с кандидатами из расширенного скана.

Запуск (cron/systemd раз в день):
    cd /root/prop-desk/strategy_combine && python code/daily_generator.py

Порядок:
  (a) Пополняет signal pool из waitlist
  (b) Ротирует stale стратегии через rotate_signal_pool()
  (c) Резолюция конфликтов через signal_conflicts()
  (d) Прунит waitlist
  (e) Логирует результат
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

COMBINE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = COMBINE_DIR / "core"
STATE_DIR = COMBINE_DIR / "state"
LOG_DIR = Path("/root/prop-desk/logs")

sys.path.insert(0, str(CORE_DIR))
import registry  # noqa: E402
import config as cfg_mod  # noqa: E402

# lazy import — config может не загрузиться без tinkoff.env, fallback на defaults
try:
    cfg = cfg_mod.load_config()
except (FileNotFoundError, json.JSONDecodeError, AssertionError, KeyError):
    cfg = None


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%F %T")
    line = "%s %s" % (ts, msg)
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "daily_generator.log", "a") as f:
        f.write(line + "\n")


def fill_from_waitlist(waitlist: dict, signal_pool: dict, pool_max: int,
                       now: float, min_rank: float = 100.0) -> int:
    """Пополняет signal pool из waitlist. Возвращает кол-во добавленных."""
    added = 0
    pool_active = sum(1 for p in signal_pool["strategies"].values()
                      if p.get("status") == "active")

    while pool_active < pool_max:
        best_cand = registry.best_candidate(waitlist)
        if not best_cand:
            break
        cand_id, cand = best_cand
        if cand.get("rank_score", 0) < min_rank:
            _log("DWAITLIST: кандидат %s/%s не прошёл порог (score=%.0f < %.0f)" % (
                cand["ticker"], cand["strategy"], cand.get("rank_score", 0), min_rank))
            break

        pool_id = "%s__%s" % (cand["ticker"], cand["strategy"])
        if pool_id in signal_pool["strategies"]:
            existing = signal_pool["strategies"][pool_id]
            if existing.get("status") in ("promoted", "rotated_out", "stale"):
                # стратегия была удалена — пересоздаём
                existing["status"] = "active"
                existing["metrics"] = cand.get("metrics", {})
                existing["rank_score"] = cand.get("rank_score", 0)
                existing["params"] = cand.get("params", {})
                existing["go_rub"] = cand.get("go_rub", existing.get("go_rub", 2000.0))
                existing["last_signal_ts"] = now
            elif cand.get("rank_score", 0) > existing.get("rank_score", 0):
                existing["metrics"] = cand.get("metrics", {})
                existing["rank_score"] = cand.get("rank_score", 0)
                existing["params"] = cand.get("params", {})
                existing["go_rub"] = cand.get("go_rub", existing.get("go_rub", 2000.0))
                existing["last_signal_ts"] = now
                _log("POOL: обновлён %s/%s (score=%.0f)" % (
                    cand["ticker"], cand["strategy"], cand.get("rank_score", 0)))
            del waitlist["candidates"][cand_id]
            pool_active += 1
            continue

        registry.add_to_signal_pool(
            signal_pool, pool_id, cand["ticker"], cand["strategy"],
            cand.get("params", {}), cand.get("metrics", {}),
            cand.get("rank_score", 0), cand.get("go_rub", 2000.0))
        del waitlist["candidates"][cand_id]
        pool_active += 1
        added += 1
        _log("POOL: добавлен %s/%s (score=%.0f)" % (
            cand["ticker"], cand["strategy"], cand.get("rank_score", 0)))

    return added


def rotate_pool(signal_pool: dict, pool_max: int, rotation_days: int,
                now: float) -> int:
    """Ротация stale стратегий. Возвращает кол-во удалённых."""
    last_rot = signal_pool.get("last_rotation_ts", 0)
    if now - last_rot < rotation_days * 86400:
        return 0
    removed = registry.rotate_signal_pool(signal_pool, pool_max, now)
    if removed:
        _log("POOL: ротация — удалено %d стратегий" % removed)
    return removed


def resolve_conflicts(portfolio: dict, signal_pool: dict) -> int:
    """Резолюция конфликтов: несколько стратегий на один тикер.

    Лучший по rank_score остаётся, остальные помечаются resolved_conflict.
    Возвращает кол-во конфликтов.
    """
    conflicts = registry.signal_conflicts(portfolio, signal_pool)
    total_resolved = 0
    for ticker, entries in conflicts.items():
        best_pid, best_entry = entries[0]  # уже отсортированы по rank_score desc
        for pid, entry in entries[1:]:
            if entry.get("status") == "active":
                entry["status"] = "resolved_conflict"
                entry["resolved_by"] = best_pid
                total_resolved += 1
                _log("POOL: конфликт %s — %s/%s (score %.0f) уступает %s/%s (score %.0f)" % (
                    ticker, entry["ticker"], entry["strategy"], entry["rank_score"],
                    best_entry["ticker"], best_entry["strategy"], best_entry["rank_score"]))
    return total_resolved


def prune_waitlist(waitlist: dict, max_size: int) -> int:
    """Удаляет просроченных и обрезает до max_size. Возвращает кол-во удалённых."""
    before = len(waitlist["candidates"])
    for cid in list(waitlist["candidates"].keys()):
        cand = waitlist["candidates"][cid]
        age_days = (time.time() - cand.get("retested_ts", cand.get("added_ts", 0))) / 86400
        if age_days > cand.get("ttl_days", 7):
            del waitlist["candidates"][cid]
    registry.prune_waitlist(waitlist, max_size)
    after = len(waitlist["candidates"])
    return before - after


def main():
    portfolio = registry.load_portfolio()
    waitlist = registry.load_waitlist()
    signal_pool = registry.load_signal_pool()
    now = time.time()

    pool_max = 10
    min_rank = 100.0
    rotation_days = 3
    waitlist_max = 20

    if cfg:
        pool_max = cfg.risk.signal_pool_max
        min_rank = cfg.risk.signal_min_rank
        rotation_days = cfg.risk.signal_rotation_days
        waitlist_max = cfg.risk.waitlist_max

    # (a) Пополнение из waitlist
    added = fill_from_waitlist(waitlist, signal_pool, pool_max, now, min_rank)

    # (b) Ротация stale
    rotated = rotate_pool(signal_pool, pool_max, rotation_days, now)

    # Повторное пополнение после ротации
    if rotated > 0:
        added += fill_from_waitlist(waitlist, signal_pool, pool_max, now, min_rank)

    # (c) Резолюция конфликтов
    conflicts_resolved = resolve_conflicts(portfolio, signal_pool)

    # (d) Prune waitlist
    pruned = prune_waitlist(waitlist, waitlist_max)

    # (e) Сохраняем
    registry.save_waitlist(waitlist)
    registry.save_signal_pool(signal_pool)

    pool_active = sum(1 for p in signal_pool["strategies"].values()
                      if p.get("status") == "active")
    pool_total = len(signal_pool["strategies"])

    _log("GENERATOR DONE: POOL=%d/%d active, added=%d, rotated=%d, "
         "conflicts=%d, pruned=%d, waitlist=%d" % (
             pool_active, pool_max, added, rotated,
             conflicts_resolved, pruned, len(waitlist["candidates"])))

    print("POOL=%d/%d active, conflicts=%d, rotated=%d" % (
        pool_active, pool_max, conflicts_resolved, rotated))


if __name__ == "__main__":
    main()
