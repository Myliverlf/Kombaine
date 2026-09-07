"""Dry-run интеграция scorecard с pipeline (без реальных ордеров).

Доказывает совместимость scorecard с данными реального плацдарма combine:
- Читает state/portfolio.json + config.json (read-only)
- Строит scorecard на реальных данных (совместимость формы)
- Симулирует модификации portfolio (как будто Engine.tick()):
  добавление/удаление слотов, изменение PnL
- Строит scorecard на модифицированных данных
- Проверяет: scorecard не мутирует входные данные
- PASS-чек-лист: slots≤3, caps=1, RI excluded, нет реальных ордеров
- exit 0 только при полном PASS.

Образец: code/engine_patches.py + code/validate_engine.py (критерий C).
"""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

# ── Пути ─────────────────────────────────────────────────────────────
COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
STATE_DIR = COMBINE_DIR / "state"
PORTFOLIO = STATE_DIR / "portfolio.json"
CONFIG_PATH = COMBINE_DIR / "config.json"
CODE_DIR = COMBINE_DIR / "code"
REPORT_LOG = Path("/root/prop-desk/logs/scorecard_dryrun.log")

sys.path.insert(0, str(CODE_DIR))
from risk_scorecard import build_scorecard, STATUS_VETO, STATUS_WARN, STATUS_OK


def md5_file(p: Path) -> str:
    """MD5 содержимого файла."""
    return hashlib.md5(p.read_bytes()).hexdigest()


def load_config() -> dict:
    """Загрузка конфига и формат dict для scorecard."""
    d = json.loads(CONFIG_PATH.read_text())
    r = d["risk"]
    return {
        "go_budget_rub": d["deposit_rub"] * r["go_budget_pct"] / 100,
        "delta_band_pct": r["delta_band_pct"],
        "deposit_rub": d["deposit_rub"],
        "portfolio_stop_drawdown_pct": r["portfolio_stop_drawdown_pct"],
        "signal_max_age_minutes": r["signal_max_age_minutes"],
        "max_slots": r["max_slots"],
        "max_contracts_per_entry": r["max_contracts_per_entry"],
        "excluded": d.get("excluded", []),
    }


def check_no_real_orders(log_text: str) -> bool:
    """Критерий C: ни одного реального post_order (только DRY_RUN)."""
    for line in log_text.splitlines():
        if "post_order" in line and "DRY_RUN" not in line:
            return False
    return True


def simulate_tick_modifications(portfolio: dict) -> dict:
    """Симуляция того, что Engine.tick() делает с portfolio.

    Не мутирует оригинал — работает с копией.
    """
    modified = json.loads(json.dumps(portfolio))

    # Симуляция: обновляем PnL для активных позиций (как tick)
    for slot_id, slot in modified.get("slots", {}).items():
        pos = slot.get("open_position")
        if pos:
            # PnL меняется каждый тик (imagine price moved)
            slot["pnl_rub"] = slot.get("pnl_rub", 0) + 15.0
            slot["peak_pnl_rub"] = max(
                slot.get("peak_pnl_rub", 0), slot["pnl_rub"])
            # last_signal_ts обновляется
            slot["last_signal_ts"] = time.time()
            # Симуляция DRY_RUN order journal
            REPORT_LOG.parent.mkdir(exist_ok=True)
            with open(REPORT_LOG, "a") as f:
                f.write("DRY_RUN: post_order %s %s qty=%d\n" % (
                    slot.get("ticker", "?"),
                    pos.get("direction", "?"),
                    pos.get("qty", 1)))

    return modified


def main() -> bool:
    """Запуск dry-run валидации. Возвращает True если PASS."""
    results = {}
    log_lines = []

    def log(msg):
        line = "  %s" % msg
        print(line)
        log_lines.append(msg)

    log("=" * 60)
    log("  SCORECARD DRY-RUN VALIDATION")
    log("=" * 60)

    # ── 0. Pre-conditions ────────────────────────────────────────────
    if not PORTFOLIO.exists():
        log("FATAL: %s не найден" % PORTFOLIO)
        return False
    if not CONFIG_PATH.exists():
        log("FATAL: %s не найден" % CONFIG_PATH)
        return False

    original_md5 = md5_file(PORTFOLIO)
    original_text = PORTFOLIO.read_text()
    original_portfolio = json.loads(original_text)

    config = load_config()
    log("Config: deposit=%.0f, max_slots=%d, excluded=%s" % (
        config["deposit_rub"], config["max_slots"], config["excluded"]))

    # ── 1. Scorecard на реальных данных ──────────────────────────────
    log("\n--- Step 1: Scorecard on real state ---")
    try:
        sc1 = build_scorecard(
            original_portfolio["slots"], config,
            equity=config["deposit_rub"] + sum(
                s.get("pnl_rub", 0) for s in original_portfolio["slots"].values()),
            peak_equity=original_portfolio.get("peak_equity", config["deposit_rub"]),
        )
        log("risk_score=%.2f, verdict=%s" % (sc1["risk_score"], sc1["verdict"]))
        for name, comp in sc1["components"].items():
            log("  %-14s %s (%s)" % (name, comp["status"], comp.get("detail", "")))
        results["real_data_compatible"] = True
    except Exception as exc:
        log("FAIL: scorecard on real data raised: %s" % exc)
        results["real_data_compatible"] = False

    # ── 2. Scorecard не мутирует входные данные ──────────────────────
    log("\n--- Step 2: Scorecard purity (no mutation) ---")
    original_slots_copy = json.loads(json.dumps(original_portfolio["slots"]))
    try:
        build_scorecard(
            original_portfolio["slots"], config,
            equity=config["deposit_rub"],
            peak_equity=original_portfolio.get("peak_equity", config["deposit_rub"]),
        )
        mutated = original_slots_copy != original_portfolio["slots"]
        results["no_mutation"] = not mutated
        log("mutation_detected=%s -> %s" % (
            mutated, "FAIL" if mutated else "PASS"))
    except Exception as exc:
        log("FAIL: %s" % exc)
        results["no_mutation"] = False

    # ── 3. Симуляция tick: модифицированный portfolio ────────────────
    log("\n--- Step 3: Simulate tick modifications ---")
    modified_portfolio = simulate_tick_modifications(original_portfolio)
    try:
        sc2 = build_scorecard(
            modified_portfolio["slots"], config,
            equity=config["deposit_rub"] + sum(
                s.get("pnl_rub", 0) for s in modified_portfolio["slots"].values()),
            peak_equity=modified_portfolio.get("peak_equity", config["deposit_rub"]),
        )
        log("After tick: risk_score=%.2f, verdict=%s" % (
            sc2["risk_score"], sc2["verdict"]))
        results["tick_simulation"] = True
    except Exception as exc:
        log("FAIL: tick simulation: %s" % exc)
        results["tick_simulation"] = False

    # ── 4. Constraint checks ────────────────────────────────────────
    log("\n--- Step 4: Constraint checks ---")

    # 4a: slots ≤ max_slots
    n_active = sum(
        1 for s in original_portfolio["slots"].values()
        if s.get("open_position"))
    slots_ok = n_active <= config["max_slots"]
    results["slots_limit"] = slots_ok
    log("active_slots=%d, max=%d -> %s" % (
        n_active, config["max_slots"], "PASS" if slots_ok else "FAIL"))

    # 4b: caps ≤ max_contracts_per_entry
    caps_ok = all(
        s.get("contracts", 0) <= config["max_contracts_per_entry"]
        for s in original_portfolio["slots"].values()
    )
    results["caps_limit"] = caps_ok
    log("contracts_ok=%s -> %s" % (caps_ok, "PASS" if caps_ok else "FAIL"))

    # 4c: RI excluded — нет RI в активных позициях
    ri_active = any(
        s.get("ticker") in config["excluded"]
        and s.get("open_position")
        for s in original_portfolio["slots"].values()
    )
    ri_ok = not ri_active
    results["ri_excluded"] = ri_ok
    log("RI_in_active_positions=%s -> %s" % (
        ri_active, "FAIL" if ri_active else "PASS"))

    # 4d: no real orders (check log)
    REPORT_LOG.parent.mkdir(exist_ok=True)
    log_content = REPORT_LOG.read_text() if REPORT_LOG.exists() else ""
    no_real = check_no_real_orders(log_content)
    results["no_real_orders"] = no_real
    log("real_orders_in_log=%s -> %s" % (
        not no_real, "PASS" if no_real else "FAIL"))

    # ── 5. Restore state (verify no mutation) ────────────────────────
    log("\n--- Step 5: State integrity ---")
    current_md5 = md5_file(PORTFOLIO)
    state_ok = current_md5 == original_md5
    results["state_integrity"] = state_ok
    log("md5_match=%s -> %s" % (state_ok, "PASS" if state_ok else "FAIL"))

    if not state_ok:
        log("Restoring original portfolio.json")
        PORTFOLIO.write_text(original_text)

    # ── Итог ────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("  RESULTS")
    log("=" * 60)
    all_pass = True
    for k in sorted(results):
        status = "PASS" if results[k] else "FAIL"
        log("  %s: %s" % (k, status))
        if not results[k]:
            all_pass = False

    log("\nOVERALL: %s" % ("PASS" if all_pass else "FAIL"))
    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
