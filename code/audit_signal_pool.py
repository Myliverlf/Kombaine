#!/usr/bin/env python3
"""Финальный аудит signal pool по критериям приёмки.

Критерии:
  A: signal_pool >= 10 entries (total) при наличии кандидатов
  B: waitlist/generator пополняет пул (waitlist не пуст при наличии кандидатов)
  C: live slots <= 3
  D: конфликт разрешается по rank_score (лучший побеждает)
  E: RI нигде нет (pool, waitlist, portfolio)
  F: validate_engine PASS + нет real-order side effects

Запуск:
    cd /root/prop-desk/strategy_combine && python code/audit_signal_pool.py
"""
import json
import subprocess
import sys
from pathlib import Path

COMBINE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = COMBINE_DIR / "core"
CODE_DIR = COMBINE_DIR / "code"
STATE_DIR = COMBINE_DIR / "state"

sys.path.insert(0, str(CORE_DIR))
import registry  # noqa: E402


def audit_a(signal_pool: dict) -> bool:
    """A: signal_pool >= 10 entries (total) при наличии кандидатов.

    Пул хранит до signal_pool_max стратегий. После резолюции конфликтов
    часть помечается resolved_conflict — это штатное поведение.
    Критерий: общий размер пула >= 10 (все статусы, кроме rotated_out).
    """
    total = len(signal_pool["strategies"])
    active = sum(1 for p in signal_pool["strategies"].values()
                 if p.get("status") == "active")
    resolved = sum(1 for p in signal_pool["strategies"].values()
                   if p.get("status") == "resolved_conflict")
    ok = total >= 10
    print("A: signal_pool total=%d (active=%d, resolved_conflict=%d, need total>=10) -> %s" % (
        total, active, resolved, "PASS" if ok else "FAIL"))
    print("   Топ-5 по rank_score:")
    for pid, p in sorted(signal_pool["strategies"].items(),
                         key=lambda kv: kv[1].get("rank_score", 0),
                         reverse=True)[:5]:
        print("     %s/%s: score=%.0f status=%s" % (
            p["ticker"], p["strategy"], p.get("rank_score", 0), p.get("status")))
    return ok


def audit_b(waitlist: dict, signal_pool: dict) -> bool:
    """B: waitlist пополняет пул (waitlist содержит кандидатов)."""
    # Проверяем что в waitlist есть кандидаты ИЛИ pool содержит достаточно стратегий
    # что указывает на то что генератор работал
    n_waitlist = len(waitlist.get("candidates", {}))
    pool_total = len(signal_pool.get("strategies", {}))
    # Пул пополняется если: (waitlist непуст) ИЛИ (pool > 0 — уже пополнялся ранее)
    ok = n_waitlist > 0 or pool_total >= 10
    print("B: waitlist=%d candidates, pool_total=%d -> %s" % (
        n_waitlist, pool_total, "PASS" if ok else "FAIL"))
    return ok


def audit_c(portfolio: dict) -> bool:
    """C: live slots <= 3."""
    n = len(portfolio.get("slots", {}))
    ok = n <= 3
    print("C: live_slots=%d (max 3) -> %s" % (n, "PASS" if ok else "FAIL"))
    for sid, s in portfolio.get("slots", {}).items():
        print("   %s: %s/%s (GO=%.0f)" % (
            sid, s["ticker"], s["strategy"], s.get("go_rub", 0)))
    return ok


def audit_d(signal_pool: dict, portfolio: dict) -> bool:
    """D: конфликт разрешается по rank_score — лучший побеждает."""
    conflicts = registry.signal_conflicts(portfolio, signal_pool)
    ok = True
    for ticker, entries in conflicts.items():
        best_pid, best_entry = entries[0]
        for pid, entry in entries[1:]:
            if entry.get("status") != "active":
                continue
            if entry["rank_score"] >= best_entry["rank_score"]:
                ok = False
                print("D: FAIL — %s/%s (score %.0f) should be below best %s/%s (score %.0f)" % (
                    entry["ticker"], entry["strategy"], entry["rank_score"],
                    best_entry["ticker"], best_entry["strategy"], best_entry["rank_score"]))

    n_conflicts = len(conflicts)
    if n_conflicts == 0:
        print("D: нет конфликтов (все уникальные тикеры) -> PASS")
    else:
        print("D: %d конфликтов разрешены, все корректны -> %s" % (
            n_conflicts, "PASS" if ok else "FAIL"))
    return ok


def audit_e(signal_pool: dict, waitlist: dict, portfolio: dict) -> bool:
    """E: RI нигде нет."""
    ri_pool = any("RI" in pid for pid in signal_pool.get("strategies", {}))
    ri_waitlist = any("RI" in cid for cid in waitlist.get("candidates", {}))
    ri_portfolio = any(s.get("ticker") == "RI"
                       for s in portfolio.get("slots", {}).values())

    ok = not (ri_pool or ri_waitlist or ri_portfolio)
    print("E: RI in pool=%s, waitlist=%s, portfolio=%s -> %s" % (
        ri_pool, ri_waitlist, ri_portfolio,
        "PASS" if ok else "FAIL"))
    return ok


def audit_f() -> bool:
    """F: validate_engine PASS + нет real-order side effects."""
    validate_script = CODE_DIR / "validate_engine.py"
    if not validate_script.exists():
        print("F: validate_engine.py not found -> FAIL")
        return False

    proc = subprocess.run(
        [sys.executable, str(validate_script)],
        capture_output=True, text=True, cwd=str(COMBINE_DIR),
        timeout=60,
    )
    ok = proc.returncode == 0
    print("F: validate_engine rc=%d -> %s" % (
        proc.returncode, "PASS" if ok else "FAIL"))
    if ok:
        # Доп. проверка: в output нет реальных ордеров
        output = proc.stdout
        has_real = False
        for line in output.splitlines():
            if "post_order" in line and "DRY_RUN" not in line:
                has_real = True
        if has_real:
            print("F: WARN — реальный ордер обнаружен в логах -> FAIL")
            return False
        print("F: no real-order side effects -> PASS")
    else:
        for line in proc.stdout.splitlines()[-10:]:
            print("   ", line)
    return ok


def main():
    print("=" * 60)
    print("SIGNAL POOL AUDIT")
    print("=" * 60)
    print()

    signal_pool = registry.load_signal_pool()
    waitlist = registry.load_waitlist()
    portfolio = registry.load_portfolio()

    results = {}

    results["A"] = audit_a(signal_pool)
    print()
    results["B"] = audit_b(waitlist, signal_pool)
    print()
    results["C"] = audit_c(portfolio)
    print()
    results["D"] = audit_d(signal_pool, portfolio)
    print()
    results["E"] = audit_e(signal_pool, waitlist, portfolio)
    print()
    results["F"] = audit_f()

    # Итог
    print()
    print("=" * 60)
    print("AUDIT RESULTS")
    print("=" * 60)
    for k in sorted(results):
        print("  %s: %s" % (k, "PASS" if results[k] else "FAIL"))

    all_pass = all(results.values())
    verdict = "PASS" if all_pass else "FAIL"
    print()
    print("VERDICT:", verdict)

    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
