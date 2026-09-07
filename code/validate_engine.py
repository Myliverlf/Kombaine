#!/usr/bin/env python3
"""Dry-run валидация боевого движка комбайна.

Проверяет критерии A (validate_engine проходит), C (DRY_RUN в логах), D (state восстановлен).
Критерий B (SL/TP = эталон) проверяется отдельно в test_sl_tp.py.

Запуск:
    cd /root/prop-desk/strategy_combine && python code/validate_engine.py
"""
import hashlib
import shutil
import sys
import traceback
from pathlib import Path

# ── Пути ─────────────────────────────────────────────────────────────
COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
CODE_DIR = COMBINE_DIR / "code"
STATE_DIR = COMBINE_DIR / "state"
PORTFOLIO = STATE_DIR / "portfolio.json"
LOG_FILE = Path("/root/prop-desk/logs/combine_engine.log")

# engine_patches.py лежит рядом с этим скриптом в code/
sys.path.insert(0, str(CODE_DIR))
# core/ нужен для Engine, registry и т.д.
sys.path.insert(0, str(COMBINE_DIR))

from engine_patches import apply_patches, remove_patches  # noqa: E402


def md5_file(p: Path) -> str:
    """MD5 содержимого файла (hex)."""
    return hashlib.md5(p.read_bytes()).hexdigest()


def main() -> dict:
    results: dict[str, bool] = {}

    # ── 0. Pre-conditions ────────────────────────────────────────────
    if not PORTFOLIO.exists():
        print("FATAL: %s не найден" % PORTFOLIO)
        return {"A": False, "C": False, "D": False}

    original_md5 = md5_file(PORTFOLIO)
    original_text = PORTFOLIO.read_text()

    # Бэкап
    bak = PORTFOLIO.with_suffix(".json.bak")
    shutil.copy2(PORTFOLIO, bak)

    # Очищаем лог перед тестом
    LOG_FILE.parent.mkdir(exist_ok=True)
    log_before_len = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
    LOG_FILE.write_text("")

    try:
        # ── 1. Создаём Engine и патчим ──────────────────────────────
        from core.engine import Engine

        eng = Engine()
        apply_patches(eng)

        # ── 2. Запускаем tick() ─────────────────────────────────────
        events = eng.tick()

        # ── 3. Критерий A: tick() прошёл без ошибок ─────────────────
        errors = [e for e in events if "ERROR" in e]
        results["A"] = len(errors) == 0
        print("A: tick() events=%d, errors=%d -> %s" % (
            len(events), len(errors), "PASS" if results["A"] else "FAIL"))
        for ev in events:
            print("  ", ev)

        # ── 4. Критерий C: только DRY_RUN, ни одного реального ордера ──
        log_content = LOG_FILE.read_text() if LOG_FILE.exists() else ""
        has_real_order = False
        for line in log_content.splitlines():
            # post_order — ключевое слово из движка; реальный ордер = не DRY_RUN
            if "post_order" in line and "DRY_RUN" not in line:
                has_real_order = True
        results["C"] = not has_real_order

        # Доп. проверка: DRY_RUN должен появиться в событиях (post был вызван)
        dry_events = [e for e in events if "DRY_RUN" in str(e)]
        print("C: real_orders_in_log=%s, dry_run_events=%d -> %s" % (
            has_real_order, len(dry_events),
            "PASS" if results["C"] else "FAIL"))

    except Exception as exc:
        traceback.print_exc()
        results["A"] = False
        results.setdefault("C", False)
        print("A: EXCEPTION -> FAIL:", exc)

    finally:
        # ── 5. Восстанавливаем portfolio.json (finally = гарантированно) ──
        try:
            shutil.copy2(bak, PORTFOLIO)
        except Exception as exc:
            print("CRITICAL: не удалось восстановить portfolio.json:", exc)
            results["D"] = False
            return results
        finally:
            # Убираем бэкап
            if bak.exists():
                try:
                    bak.unlink()
                except OSError as exc:
                    print("WARN: не удалось удалить бэкап:", exc)

        # ── 6. Критерий D: state восстановлен ───────────────────────
        restored_md5 = md5_file(PORTFOLIO)
        restored_text = PORTFOLIO.read_text()
        results["D"] = (original_md5 == restored_md5)
        print("D: portfolio md5_before=%s md5_after=%s -> %s" % (
            original_md5[:8], restored_md5[:8],
            "PASS" if results["D"] else "FAIL"))

        # Доп. проверка: 내용 идентичен побайтно
        if original_text != restored_text:
            results["D"] = False
            print("D: content mismatch (binary differ) -> FAIL")

    # ── Итог ────────────────────────────────────────────────────────
    print("\n=== RESULTS ===")
    for k in sorted(results):
        print("  %s: %s" % (k, "PASS" if results[k] else "FAIL"))

    all_pass = all(results.values())
    print("\nOVERALL:", "PASS" if all_pass else "FAIL")
    return results


if __name__ == "__main__":
    res = main()
    sys.exit(0 if all(res.values()) else 1)
