#!/usr/bin/env python3
"""Seed Pool: заполняет waitlist и signal pool кандидатами из расширенного скана.

Standalone скрипт без внешних зависимостей (Tinkoff API, futures_lab).
Использует registry.py для CRUD с state-файлами.

Источник данных: futures_extended_15m_20260819.scan_results.json (21 non-RI quality-passed).

Запуск:
    cd /root/prop-desk/strategy_combine && python code/seed_pool.py
"""
import json
import sys
from pathlib import Path

COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
CORE_DIR = COMBINE_DIR / "core"
SCAN_FILE = Path("/root/prop-desk/strategies/futures_extended_15m_20260819.scan_results.json")

sys.path.insert(0, str(CORE_DIR))
import registry  # noqa: E402

# live ГО по тикерам (из config.json / актуальных данных)
GO_LIVE = {
    "SBER": 4702.0,
    "GAZP": 1433.0,
    "LKOH": 7000.0,
    "Si": 13120.0,
    "BR": 3500.0,
}


def rank_score(m: dict) -> float:
    """Ранжирующий score: профит + качество, штрафы за редкость.

    Тот же алгоритм что в core/seeder.py — единая метрика для всего пайплайна.
    """
    pnl = m.get("pnl", 0.0)
    sharpe = m.get("sharpe", 0.0)
    win = m.get("win_rate", 0.0)
    pf = m.get("pf", 1.0)
    tpd = m.get("trades_per_day", 0.0)
    freq_bonus = 500.0 if 0.8 <= tpd <= 2.5 else (250.0 if tpd >= 0.4 else -300.0)
    return pnl + sharpe * 1000.0 + win * 30.0 + (pf - 1) * 800.0 + freq_bonus


def map_scan_to_metrics(row: dict) -> dict:
    """Маппинг полей расширенного скана на формат metrics registry.py."""
    return {
        "pnl": row.get("wf_avg_total_pnl", 0.0),
        "sharpe": row.get("wf_avg_sharpe", 0.0),
        "win_rate": row.get("wf_avg_win_rate", 0.0),
        "pf": row.get("wf_avg_profit_factor", 1.0),
        "dd": row.get("wf_avg_drawdown", 0.0),
        "trades": row.get("wf_median_trade_count", 0),
        "trades_per_day": row.get("trades_per_day", 0.0),
    }


def load_candidates() -> list:
    """Загрузка кандидатов из расширенного скана: только non-RI + quality-passed."""
    data = json.loads(SCAN_FILE.read_text())
    rows = data if isinstance(data, list) else data.get("results", [])
    return [r for r in rows
            if r.get("ticker") != "RI" and r.get("wf_quality_passed")]


def main():
    cands = load_candidates()
    print("кандидатов из скана (non-RI, quality-passed):", len(cands))

    waitlist = registry.load_waitlist()
    signal_pool = registry.load_signal_pool()
    pool_max = 10  # signal_pool_max из config.json

    added_waitlist = 0
    added_pool = 0
    skipped_existing = 0

    for r in sorted(cands, key=lambda x: x.get("wf_avg_total_pnl", 0), reverse=True):
        ticker = r["ticker"]
        strategy = r["strategy"]
        params = r.get("best_params", {}) or {}
        cid = "%s__%s" % (ticker, strategy)

        # Пропускаем если уже есть в waitlist
        if cid in waitlist["candidates"]:
            skipped_existing += 1
            continue

        metrics = map_scan_to_metrics(r)
        score = rank_score(metrics)
        go = GO_LIVE.get(ticker, 2000.0)

        # Добавляем в waitlist
        registry.add_candidate(waitlist, cid, ticker, strategy, params,
                               metrics=metrics, rank_score=score, ttl_days=7)
        waitlist["candidates"][cid]["go_rub"] = go
        added_waitlist += 1

        # Добавляем в signal pool если прошёл порог и есть место
        pool_active = sum(1 for p in signal_pool["strategies"].values()
                          if p.get("status") == "active")
        if pool_active >= pool_max:
            continue

        pool_id = "%s__%s" % (ticker, strategy)
        if pool_id in signal_pool["strategies"]:
            existing = signal_pool["strategies"][pool_id]
            if score > existing.get("rank_score", 0):
                existing["metrics"] = metrics
                existing["rank_score"] = score
                existing["params"] = params
                existing["go_rub"] = go
                existing["last_signal_ts"] = __import__("time").time()
                print("  обновлён в pool: %s/%s (score=%.0f)" % (ticker, strategy, score))
            continue

        registry.add_to_signal_pool(signal_pool, pool_id, ticker, strategy,
                                    params, metrics, score, go)
        added_pool += 1
        print("  добавлен в pool: %s/%s (score=%.0f)" % (ticker, strategy, score))

    registry.save_waitlist(waitlist)
    registry.save_signal_pool(signal_pool)

    pool_active_final = sum(1 for p in signal_pool["strategies"].values()
                            if p.get("status") == "active")
    print("\n=== Seed Pool итог ===")
    print("waitlist: +%d новых (пропущено %d существующих), всего %d" % (
        added_waitlist, skipped_existing, len(waitlist["candidates"])))
    print("signal pool: +%d новых, active=%d (max=%d)" % (
        added_pool, pool_active_final, pool_max))
    print("pool стратегий:")
    for pid, p in sorted(signal_pool["strategies"].items(),
                         key=lambda kv: kv[1].get("rank_score", 0), reverse=True):
        status = p.get("status", "?")
        print("  %s/%s: score=%.0f status=%s" % (
            p["ticker"], p["strategy"], p.get("rank_score", 0), status))


if __name__ == "__main__":
    main()
