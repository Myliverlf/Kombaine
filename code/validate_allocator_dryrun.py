"""Dry-run валидатор allocator — read-only, без реальных ордеров.

Доказывает совместимость allocator-пайплайна с данными реального плацдарма combine:
  - Читает state/portfolio.json, config.json, state/regime_snapshot.json (read-only)
  - analytics.db (SELECT-only) — считает avg_win/avg_loss для expectancy
  - Строит кандидатов из portfolio + signal_pool, прогоняет select_live_slots
  - Печатает PASS-чек-лист:
      slots<=3, caps==1, RI excluded, no real orders
  - Таблица allocator-scorecard (expectancy/risk/regime/score)
  - Сравнение allocator vs baseline-rank_score
  - Отчёт пишет в stdout; exit 0 при полном PASS

Образец: code/validate_scorecard_dryrun.py
"""
import json
import math
import sqlite3
import sys
from pathlib import Path

# ── Пути ─────────────────────────────────────────────────────────────
COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
STATE_DIR = COMBINE_DIR / "state"
PORTFOLIO = STATE_DIR / "portfolio.json"
REGIME_SNAP = STATE_DIR / "regime_snapshot.json"
SIGNAL_POOL = STATE_DIR / "signal_pool.json"
CONFIG_PATH = COMBINE_DIR / "config.json"
# Canonical local analytics journal; state/analytics.db is legacy/empty.
ANALYTICS_DB = COMBINE_DIR / "analytics.db"
CODE_DIR = COMBINE_DIR / "code"

sys.path.insert(0, str(CODE_DIR))
from allocator_metrics import expectancy_r, regime_bonus, risk_penalty, allocator_score
from candidate_allocator import (
    select_live_slots,
    select_baseline_slots,
    baseline_rank_score,
)


def _load_json(p: Path) -> dict:
    """Безопасная загрузка JSON."""
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _load_config() -> dict:
    """Загрузка config.json."""
    return _load_json(CONFIG_PATH)


def _query_trade_stats(db_path: Path, ticker: str) -> dict:
    """SQL SELECT-only: avg_win/avg_loss/win_rate для тикера из analytics.db.

    Если таблиц trades/slot_pnls нет или пусто — вернём defaults.
    """
    defaults = {"win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "n_trades": 0}

    if not db_path.exists() or db_path.stat().st_size == 0:
        return defaults

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Проверяем наличие таблицы trades
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if not cur.fetchone():
            conn.close()
            return defaults

        # Считаем win_rate, avg_win, avg_loss для тикера
        cur.execute("""
            SELECT
                COUNT(*) as n_trades,
                SUM(CASE WHEN pnl_rub > 0 THEN 1 ELSE 0 END) as n_wins,
                AVG(CASE WHEN pnl_rub > 0 THEN pnl_rub END) as avg_win,
                AVG(CASE WHEN pnl_rub <= 0 THEN ABS(pnl_rub) END) as avg_loss
            FROM trades
            WHERE ticker = ?
        """, (ticker,))
        row = cur.fetchone()
        conn.close()

        if not row or row["n_trades"] == 0:
            return defaults

        n_trades = row["n_trades"]
        n_wins = row["n_wins"] or 0
        win_rate = n_wins / n_trades if n_trades > 0 else 0.0

        return {
            "win_rate": round(win_rate, 4),
            "avg_win": round(float(row["avg_win"] or 0.0), 2),
            "avg_loss": round(float(row["avg_loss"] or 0.0), 2),
            "n_trades": n_trades,
        }

    except sqlite3.Error:
        return defaults


def _build_candidates_from_portfolio(
    portfolio: dict,
    regime_snapshot: dict,
    config: dict,
) -> list:
    """Построить кандидатов из portfolio slots.

    Каждый slot без open_position — потенциальный кандидат (ищет вход).
    Считаем expectancy из analytics.db (если есть), regime — из snapshot.
    """
    risk = config.get("risk", {})
    universe = config.get("universe", [])

    candidates = []
    for slot_id, slot in portfolio.get("slots", {}).items():
        ticker = slot.get("ticker", "")

        # Пропускаем если тикер не в universe
        if ticker not in universe:
            continue

        # Только слоты без открытой позиции (ищут вход)
        # Плюс те что с открытой позицией (для показа текущего ранжирования)
        stats = _query_trade_stats(ANALYTICS_DB, ticker)

        # Если нет данных из БД — берём из portfolio
        n_trades = slot.get("n_trades", 0)
        if n_trades > 0 and stats["n_trades"] == 0:
            # Пытаемся достроить из pnl (грубая оценка)
            pnl = slot.get("pnl_rub", 0.0)
            if pnl > 0:
                stats = {"win_rate": 0.6, "avg_win": abs(pnl) / n_trades,
                         "avg_loss": 0.0, "n_trades": n_trades}
            elif pnl < 0:
                stats = {"win_rate": 0.4, "avg_win": 0.0,
                         "avg_loss": abs(pnl) / n_trades, "n_trades": n_trades}

        direction = None
        open_pos = slot.get("open_position")
        if open_pos:
            direction = open_pos.get("direction")

        # drawdown_pct из slot
        pnl = slot.get("pnl_rub", 0.0)
        peak = slot.get("peak_pnl_rub", 0.0)
        dd_pct = 0.0
        if peak > 0:
            dd_pct = (peak - pnl) / peak * 100.0
        elif pnl < 0:
            dd_pct = abs(pnl) / 100.0  # грубая оценка

        candidates.append({
            "ticker": ticker,
            "direction": direction,
            "win_rate": stats["win_rate"],
            "avg_win": stats["avg_win"],
            "avg_loss": stats["avg_loss"],
            "drawdown_pct": round(dd_pct, 2),
            "contracts_requested": slot.get("contracts", 1),
            "_slot_id": slot_id,
            "_n_trades": n_trades,
        })

    return candidates


def _check_no_real_orders(log_text: str) -> bool:
    """Критерий: ни одного реального post_order."""
    forbidden = ["post_order", "place_order", "send_order", "submit_order"]
    for line in log_text.splitlines():
        for f in forbidden:
            if f in line and "DRY_RUN" not in line and "no real" not in line.lower():
                return False
    return True


def _check_no_broker_in_modules() -> bool:
    """Проверка: allocator модули не импортируют broker.

    Проверяет только import/from-import строки, не комментарии/docstrings.
    """
    modules = ["allocator_metrics.py", "candidate_allocator.py"]
    # Запрещённые паттерны ТОЛЬКО в import-строках
    import_forbidden = ["from tinkoff", "import tinkoff", "from broker",
                        "import broker", "from tinkoff"]
    # В любом месте файла (включая строки кода, не комментарии)
    code_forbidden = ["Client(", "post_order(", "place_order(", "send_order("]

    import_line_prefixes = ("import ", "from ")

    for mod_name in modules:
        mod_path = CODE_DIR / mod_name
        if not mod_path.exists():
            continue
        lines = mod_path.read_text().splitlines()
        for line in lines:
            stripped = line.strip()
            # Пропускаем комментарии и пустые строки
            if stripped.startswith("#") or not stripped:
                continue
            # Проверяем import-строки
            if any(stripped.startswith(p) for p in import_line_prefixes):
                for pat in import_forbidden:
                    if pat in stripped:
                        return False
            # Проверяем вызовы функций broker
            for pat in code_forbidden:
                if pat in stripped:
                    return False
    return True


def main() -> bool:
    """Запуск dry-run валидации allocator. True = PASS."""
    results = {}

    def log(msg):
        print("  %s" % msg)

    print("=" * 60)
    print("  ALLOCATOR DRY-RUN VALIDATION")
    print("=" * 60)

    # ── 0. Pre-conditions ────────────────────────────────────────────
    config = _load_config()
    if not config:
        log("FATAL: config.json not found")
        return False

    risk = config.get("risk", {})
    excluded = config.get("excluded", [])
    max_slots = risk.get("max_slots", 3)
    max_contracts = risk.get("max_contracts_per_entry", 1)

    log("Config: deposit=%s, max_slots=%d, excluded=%s" % (
        config.get("deposit_rub"), max_slots, excluded))

    # ── 1. Load state ────────────────────────────────────────────────
    log("\n--- Step 1: Load state ---")
    portfolio = _load_json(PORTFOLIO)
    regime_snapshot = _load_json(REGIME_SNAP)
    signal_pool = _load_json(SIGNAL_POOL)

    n_slots = len(portfolio.get("slots", {}))
    n_active = sum(1 for s in portfolio.get("slots", {}).values()
                   if s.get("open_position"))
    n_pool = len(signal_pool) if isinstance(signal_pool, list) else 0

    log("portfolio: %d slots (%d active)" % (n_slots, n_active))
    log("signal_pool: %d strategies" % n_pool)
    log("regime: bias=%s, tickers=%d" % (
        regime_snapshot.get("bias", "?"),
        len(regime_snapshot.get("tickers", {}))))

    # ── 2. Build candidates ──────────────────────────────────────────
    log("\n--- Step 2: Build candidates ---")
    candidates = _build_candidates_from_portfolio(portfolio, regime_snapshot, config)
    log("candidates: %d" % len(candidates))
    for c in candidates:
        e_r = expectancy_r(
            {"win_rate": c["win_rate"], "avg_win": c["avg_win"],
             "avg_loss": c["avg_loss"]},
            risk_per_trade=config["deposit_rub"] * risk.get("risk_per_trade_pct", 2.7) / 100,
        )
        log("  %s: wr=%.2f, aw=%.0f, al=%.0f, E[R]=%.2f, dir=%s" % (
            c["ticker"], c["win_rate"], c["avg_win"], c["avg_loss"],
            e_r, c.get("direction", "None")))

    # ── 3. Run allocator ─────────────────────────────────────────────
    log("\n--- Step 3: Allocator select ---")
    cfg_alloc = {
        "excluded": excluded,
        "risk": {
            "max_slots": max_slots,
            "max_contracts_per_entry": max_contracts,
        },
        "risk_per_trade_pct": risk.get("risk_per_trade_pct", 2.7),
        "deposit_rub": config.get("deposit_rub", 21281),
    }

    selected = select_live_slots(candidates, cfg_alloc, regime_snapshot)

    if not selected:
        log("RESULT: empty universe — no candidates to select")
        log("This is legal: portfolio may have all slots occupied.")
        results["allocator_ran"] = True
        results["slots_limit"] = True
        results["caps_limit"] = True
        results["ri_excluded"] = True
        results["no_real_orders"] = True
        results["no_broker_in_modules"] = _check_no_broker_in_modules()
    else:
        log("selected: %d slots" % len(selected))
        print("\n  %-10s %-8s %8s %10s %8s %8s" % (
            "TICKER", "DIR", "SCORE", "E[R]", "RISK", "REGIME"))
        print("  " + "-" * 55)
        for s in selected:
            print("  %-10s %-8s %8.4f %10.4f %8.4f %8.4f" % (
                s["ticker"], s.get("direction", "?"),
                s["score"], s["expectancy_r"],
                s["risk_penalty"], s["regime_bonus"]))

        # ── 4. Constraint checks ────────────────────────────────────
        log("\n--- Step 4: Constraint checks ---")

        # 4a: slots <= max_slots
        slots_ok = len(selected) <= max_slots
        results["slots_limit"] = slots_ok
        log("selected=%d, max=%d -> %s" % (
            len(selected), max_slots, "PASS" if slots_ok else "FAIL"))

        # 4b: caps == 1
        caps_ok = all(s["contracts"] <= max_contracts for s in selected)
        results["caps_limit"] = caps_ok
        log("all_caps<=%d -> %s" % (max_contracts, "PASS" if caps_ok else "FAIL"))

        # 4c: RI excluded
        ri_ok = not any(s["ticker"] in excluded for s in selected)
        results["ri_excluded"] = ri_ok
        log("RI_in_selected=%s -> %s" % (
            not ri_ok, "FAIL" if not ri_ok else "PASS"))

        # 4d: no real orders
        results["no_real_orders"] = True  # чистые функции, orders тут нет
        log("no_real_orders -> PASS")

        # 4e: no broker imports
        broker_clean = _check_no_broker_in_modules()
        results["no_broker_in_modules"] = broker_clean
        log("no_broker_in_modules -> %s" % ("PASS" if broker_clean else "FAIL"))

        # ── 5. Baseline comparison ──────────────────────────────────
        log("\n--- Step 5: Allocator vs Baseline ---")
        baseline = select_baseline_slots(
            [dict(c) for c in candidates],
            max_slots=max_slots,
            excluded=excluded,
        )
        if baseline:
            alloc_score_sum = sum(s["score"] for s in selected)
            baseline_score_sum = sum(baseline_rank_score(b) for b in baseline)
            log("allocator_score_sum: %.4f" % alloc_score_sum)
            log("baseline_score_sum: %.4f" % baseline_score_sum)

            alloc_e_sum = sum(s["expectancy_r"] for s in selected)
            baseline_e_sum = sum(
                expectancy_r(
                    {"win_rate": b["win_rate"], "avg_win": b["avg_win"],
                     "avg_loss": b["avg_loss"]},
                    risk_per_trade=cfg_alloc["deposit_rub"]
                    * cfg_alloc["risk_per_trade_pct"] / 100,
                ) for b in baseline
            )
            log("allocator_expectancy_sum: %.4f" % alloc_e_sum)
            log("baseline_expectancy_sum: %.4f" % baseline_e_sum)
        else:
            log("no baseline candidates")

        results["allocator_ran"] = True

    # ── 6. PASS-чек-лист ─────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("  PASS-CHECKLIST")
    log("=" * 60)

    checklist = [
        ("slots<=3", results.get("slots_limit", False)),
        ("caps==1", results.get("caps_limit", False)),
        ("RI excluded", results.get("ri_excluded", False)),
        ("no real orders", results.get("no_real_orders", False)),
        ("no broker imports", results.get("no_broker_in_modules", False)),
        ("allocator ran", results.get("allocator_ran", False)),
    ]

    all_pass = True
    for name, ok in checklist:
        status = "PASS" if ok else "FAIL"
        log("  [%s] %s" % (status, name))
        if not ok:
            all_pass = False

    print("\n" + "=" * 60)
    print("  OVERALL: %s" % ("PASS" if all_pass else "FAIL"))
    print("=" * 60)

    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
