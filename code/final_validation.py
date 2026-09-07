#!/usr/bin/env python3
"""Комплексная финальная валидация E2E контура strategy_combine.

Проверяет:
  1. py_compile на всех изменённых файлах
  2. E2E dry-run (14 шагов) → сравнение с бейзлайном (11 FAIL)
  3. Проверка: mode=paper, n_live_orders=0, нет broker imports
  4. Генерация verdict.md с табличным статусом по каждому критерию

Использование:
  cd /root/prop-desk/strategy_combine
  python code/final_validation.py
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = Path(_HERE).parent
_CODE_DIR = str(Path(_HERE))
_VERDICT_PATH = _PROJECT_ROOT / "verdict.md"
_E2E_RESULT_PATHS = [
    _PROJECT_ROOT / "e2e_real_result.json",
    _PROJECT_ROOT / "e2e_full_result.json",
]
_E2E_RESULT_PATH = _E2E_RESULT_PATHS[0]  # legacy compat

# Baseline from previous run
BASELINE_PASSED = 3
BASELINE_FAILED = 11


def _py_compile_check(files: List[Path]) -> Tuple[List[str], List[str]]:
    """Check py_compile on a list of files. Returns (passed, failed)."""
    passed: List[str] = []
    failed: List[str] = []
    for f in files:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(f)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                passed.append(f.name)
            else:
                failed.append(f"{f.name}: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            failed.append(f"{f.name}: TIMEOUT")
        except OSError as exc:
            failed.append(f"{f.name}: {exc}")
    return passed, failed


BROKER_IMPORT_ALLOWLIST = {
    # Read-only / explicit maintenance helpers. They do not place orders and are
    # intentionally excluded from the generic live-order import guard.
    "live_equity_probe.py",
    "post_manual_close_reset.py",
}


def _check_no_broker_imports() -> Tuple[bool, List[str]]:
    """AST-guard: scan code/ for unsafe broker/tinkoff imports."""
    broker_files: List[str] = []
    for py_file in Path(_CODE_DIR).glob("*.py"):
        if py_file.name in BROKER_IMPORT_ALLOWLIST:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        lower = alias.name.lower()
                        if "tinkoff" in lower or "broker" in lower:
                            broker_files.append(f"{py_file.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        lower = node.module.lower()
                        if "tinkoff" in lower or "broker" in lower:
                            broker_files.append(f"{py_file.name}: from {node.module}")
        except (SyntaxError, OSError) as exc:
            broker_files.append(f"{py_file.name}: parse_error={exc}")
    return len(broker_files) == 0, broker_files


def _run_e2e_dryrun() -> Dict[str, Any]:
    """Run the E2E dry-run and return the artifact."""
    script = Path(_CODE_DIR) / "e2e_real_dryrun.py"
    if not script.exists():
        return {"error": f"E2E script not found: {script}"}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=120,
            cwd=str(_PROJECT_ROOT),
        )
        # Parse the artifact from the JSON output file (fallback: try both names)
        for candidate in _E2E_RESULT_PATHS:
            if candidate.exists():
                with open(candidate, "r", encoding="utf-8") as f:
                    return json.load(f)
        # Fallback: parse stdout
        for line in result.stdout.split("\n"):
            if "RESULT:" in line or "ALL PASSED" in line or "FAILED" in line:
                pass
        return {"error": "E2E result JSON not found", "stdout_tail": result.stdout[-2000:]}
    except subprocess.TimeoutExpired:
        return {"error": "E2E dry-run TIMEOUT (120s)"}
    except OSError as exc:
        return {"error": f"E2E dry-run failed: {exc}"}


def run_validation() -> Dict[str, Any]:
    """Run full validation suite. Returns summary dict."""
    report: Dict[str, Any] = {}
    checks: List[Dict[str, Any]] = []

    # ── Check 1: py_compile on key files ──
    key_files = [
        Path(_CODE_DIR) / "e2e_real_dryrun.py",
        Path(_CODE_DIR) / "init_strategy_registry.py",
        Path(_CODE_DIR) / "final_validation.py",
        _PROJECT_ROOT / "core" / "strategy_supervisor_flow.py",
    ]
    # Also compile all code/*.py
    all_code_files = list(Path(_CODE_DIR).glob("*.py"))
    compile_files = list(set(key_files + all_code_files))

    passed_compile, failed_compile = _py_compile_check(compile_files)
    checks.append({
        "name": "py_compile",
        "passed": len(failed_compile) == 0,
        "detail": f"{len(passed_compile)}/{len(compile_files)} compiled OK",
        "failed": failed_compile[:10],  # cap for readability
    })

    # ── Check 2: No broker imports ──
    no_broker, broker_files = _check_no_broker_imports()
    checks.append({
        "name": "no_broker_imports",
        "passed": no_broker,
        "detail": "No broker/tinkoff imports in code/" if no_broker else f"Found: {broker_files[:5]}",
        "broker_files": broker_files,
    })

    # ── Check 3: E2E Dry-Run ──
    e2e_result = _run_e2e_dryrun()
    e2e_passed = e2e_result.get("n_passed", 0)
    e2e_failed = e2e_result.get("n_failed", 0)
    e2e_total = e2e_result.get("n_total", 0)
    e2e_verdict = e2e_result.get("final_verdict", "UNKNOWN")
    e2e_config_mode = e2e_result.get("config_mode", "unknown")
    e2e_live_orders = e2e_result.get("n_live_orders", 0)
    e2e_has_emergency = e2e_result.get("has_emergency_stop", False)
    has_error = "error" in e2e_result

    checks.append({
        "name": "e2e_dryrun",
        "passed": (
            not has_error
            and e2e_failed == 0
            and e2e_verdict in {"ALLOW", "REDUCE", "VETO"}
            and not e2e_result.get("contradiction", False)
        ),
        "detail": f"PASS={e2e_passed}/{e2e_total}, FAIL={e2e_failed}/{e2e_total} "
                  f"(baseline: {BASELINE_PASSED}/{BASELINE_PASSED+BASELINE_FAILED} PASS, "
                  f"{BASELINE_FAILED} FAIL), verdict={e2e_verdict}",
        "e2e_result": e2e_result,
        "improvement": f"{BASELINE_FAILED}→{e2e_failed} FAIL ({BASELINE_FAILED - e2e_failed} fewer)",
    })

    # ── Check 4: mode=paper ──
    is_paper = e2e_config_mode == "paper"
    checks.append({
        "name": "paper_mode",
        "passed": is_paper,
        "detail": f"config_mode={e2e_config_mode}",
    })

    # ── Check 5: no live orders ──
    no_live = e2e_live_orders == 0
    checks.append({
        "name": "no_live_orders",
        "passed": no_live,
        "detail": f"n_live_orders={e2e_live_orders}",
    })

    # ── Check 6: strategy_registry.json exists ──
    registry_exists = (_PROJECT_ROOT / "state" / "strategy_registry.json").exists()
    checks.append({
        "name": "strategy_registry",
        "passed": registry_exists,
        "detail": f"state/strategy_registry.json exists={registry_exists}",
    })

    # ── Check 7: core bridge works ──
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from core.strategy_supervisor_flow import write_legacy_exports; print('OK')"],
            capture_output=True, text=True, timeout=10,
            cwd=str(_PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": _CODE_DIR},
        )
        core_bridge_ok = result.returncode == 0 and "OK" in result.stdout
        core_bridge_detail = result.stdout.strip() if core_bridge_ok else result.stderr.strip()[:200]
    except (subprocess.TimeoutExpired, OSError) as exc:
        core_bridge_ok = False
        core_bridge_detail = str(exc)[:200]
    checks.append({
        "name": "core_bridge",
        "passed": core_bridge_ok,
        "detail": core_bridge_detail,
    })

    # ── Summary ──
    n_checks = len(checks)
    n_passed = sum(1 for c in checks if c["passed"])
    n_failed = n_checks - n_passed
    all_ok = n_failed == 0

    report = {
        "checks": checks,
        "n_checks": n_checks,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "all_ok": all_ok,
        "e2e_improvement": {
            "baseline_pass": BASELINE_PASSED,
            "baseline_fail": BASELINE_FAILED,
            "current_pass": e2e_passed,
            "current_fail": e2e_failed,
        },
        "timestamp": time.time(),
    }

    return report


def _verdict_header(report: Dict[str, Any]) -> str:
    """Build verdict header string."""
    if report.get('all_ok'):
        return '## Общий статус: ✅ ALL PASSED'
    n_failed = report.get('n_failed', 0)
    return f'## Общий статус: ❌ {n_failed} FAILED'


def write_verdict(report: Dict[str, Any]) -> None:
    """Generate verdict.md from validation report."""
    checks = report.get("checks", [])
    e2e = report.get("e2e_improvement", {})
    lines = [
        "# Verdict: E2E Strategy Combine — Финальная валидация",
        "",
        _verdict_header(report),
        "",
        "## Результаты проверок",
        "",
        "| # | Проверка | Статус | Детали |",
        "|---|---|---|---|",
    ]

    for i, c in enumerate(checks, 1):
        icon = "✅" if c["passed"] else "❌"
        detail = str(c.get("detail", ""))[:120]
        lines.append(f"| {i} | {c['name']} | {icon} | {detail} |")

    lines.extend([
        "",
        "## Прогресс E2E Dry-Run",
        "",
        f"| Метрика | Бейзлайн | Текущий |",
        f"|---|---|---|",
        f"| PASS | {e2e.get('baseline_pass', '?')}/{e2e.get('baseline_pass', 3)+e2e.get('baseline_fail', 11)} | {e2e.get('current_pass', '?')}/{e2e.get('current_pass', 0)+e2e.get('current_fail', 0)} |",
        f"| FAIL | {e2e.get('baseline_fail', '?')}/{e2e.get('baseline_pass', 3)+e2e.get('baseline_fail', 11)} | {e2e.get('current_fail', '?')}/{e2e.get('current_pass', 0)+e2e.get('current_fail', 0)} |",
        "",
        "## Критерии приёмки (из task.md)",
        "",
        f"1. Дыры закрываются кодом/bridge/fix — {'✅' if report.get('all_ok') else '❌'}",
        f"2. Повторный dry-run показывает меньше FAIL — "
        f"{'✅' if e2e.get('current_fail', 99) < e2e.get('baseline_fail', 11) else '⚠️ check manually'} "
        f"({e2e.get('baseline_fail', 11)}→{e2e.get('current_fail', '?')})",
        f"3. Код компилируется — "
        f"{'✅' if next((c['passed'] for c in checks if c['name'] == 'py_compile'), False) else '❌'}",
        f"4. Финальный статус есть — ✅ (этот файл)",
        f"5. Без live orders — "
        f"{'✅' if next((c['passed'] for c in checks if c['name'] == 'no_live_orders'), False) else '❌'}",
        "",
        f"---",
        f"Generated by final_validation.py at {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ])

    _VERDICT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Verdict written to: {_VERDICT_PATH}")


def main() -> None:
    """CLI entry point."""
    print("=" * 70)
    print("FINAL VALIDATION — strategy_combine E2E")
    print("=" * 70)

    report = run_validation()

    # Print summary
    for c in report["checks"]:
        icon = "✅" if c["passed"] else "❌"
        print(f"  {icon} [{c['name']}] {c.get('detail', '')}")

    print()
    e2e = report.get("e2e_improvement", {})
    print(f"  E2E improvement: {e2e.get('baseline_fail', '?')}→{e2e.get('current_fail', '?')} FAIL")

    overall = "ALL PASSED" if report["all_ok"] else f"{report['n_failed']} FAILED"
    print(f"\n  OVERALL: {overall}")
    print("=" * 70)

    # Write verdict
    write_verdict(report)

    # Exit code
    sys.exit(0 if report["all_ok"] else 1)


if __name__ == "__main__":
    main()
