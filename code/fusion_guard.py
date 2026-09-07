"""Fusion Guard — AST-guard + плацдарм-охрана для fused signals.

Модуль проверяет:
  1. AST-guard: файлы fusion не импортируют broker/client модули.
  2. Плацдарм-охрана: core/*.py и config.json не модифицированы.

Используется как pytest fixture или standalone-валидатор.

Источники:
  - strategy_ideas.py:check_no_broker_imports() — паттерн AST-guard
  - plan.md Фича 5
"""
import ast as _ast
import hashlib
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure code/ dir on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ─── Constants ─────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(_HERE)

FORBIDDEN_IMPORT_MODULES = frozenset({
    "tinkoff", "tinkoff_api", "tinkoff.invest", "tinkoff_invest",
    "futures_lab", "broker", "broker_api",
})
FORBIDDEN_NAMES = frozenset({"client", "broker", "trader", "order", "place_order"})

# Files that must NOT be modified (baseline guard)
BASELINE_PATHS = [
    "core/analytics.py",
    "core/risk.py",
    "core/registry.py",
    "core/engine.py",
    "core/config.py",
    "core/regime.py",
    "core/__init__.py",
    "config.json",
    "code/candidate_allocator.py",
    "code/pipeline_ranker.py",
    "code/allocator_metrics.py",
    "code/regime_gate.py",
    "code/risk_scorecard.py",
    "code/strategy_ideas.py",
]

# All new fusion files (to check for broker imports)
FUSION_FILES = [
    "code/signal_fusion.py",
    "code/fusion_scorecard.py",
    "code/fusion_pipeline.py",
    "code/fusion_guard.py",
]


# ─── AST broker guard ─────────────────────────────────────────────────

def check_no_broker_imports_ast(filepath: str) -> Tuple[bool, Optional[str]]:
    """AST-traversal: файл не импортирует broker/client модули.

    Возвращает (ok, error_msg). ok=True если нет запрещённых импортов.
    """
    if not os.path.isfile(filepath):
        return False, f"File not found: {filepath}"

    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = _ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return False, f"SyntaxError in {filepath}: {e}"

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                mod_name = alias.name.split(".")[0]
                if mod_name in FORBIDDEN_IMPORT_MODULES:
                    return False, (
                        f"Forbidden import '{alias.name}' at line {node.lineno}"
                    )
        elif isinstance(node, _ast.ImportFrom):
            if node.module:
                mod_root = node.module.split(".")[0]
                if mod_root in FORBIDDEN_IMPORT_MODULES:
                    return False, (
                        f"Forbidden from-import '{node.module}' at line {node.lineno}"
                    )
        elif isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id in FORBIDDEN_NAMES:
                return False, (
                    f"Forbidden call '{func.id}()' at line {node.lineno}"
                )
            if isinstance(func, _ast.Attribute) and func.attr in FORBIDDEN_NAMES:
                return False, (
                    f"Forbidden method '.{func.attr}()' at line {node.lineno}"
                )

    return True, None


def check_all_fusion_files() -> List[str]:
    """Проверить все fusion-файлы на broker imports.

    Возвращает список ошибок (пустой = OK).
    """
    errors: List[str] = []
    for rel in FUSION_FILES:
        fpath = os.path.join(PROJECT_ROOT, rel)
        ok, msg = check_no_broker_imports_ast(fpath)
        if not ok:
            errors.append(f"{rel}: {msg}")
    return errors


# ─── Baseline hash guard ──────────────────────────────────────────────

def _file_hash(filepath: str) -> str:
    """SHA-256 хеш файла."""
    if not os.path.isfile(filepath):
        return "MISSING"
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def compute_baseline_hashes(
    paths: Optional[List[str]] = None,
    root: Optional[str] = None,
) -> Dict[str, str]:
    """Вычислить хеши baseline-файлов.

    Возвращает {relative_path: sha256_hex}.
    """
    root = root or PROJECT_ROOT
    files = paths or BASELINE_PATHS
    hashes: Dict[str, str] = {}
    for rel in files:
        fpath = os.path.join(root, rel)
        hashes[rel] = _file_hash(fpath)
    return hashes


# Baseline hashes computed at import time (.freeze state)
_BASELINE_HASHES: Optional[Dict[str, str]] = None


def snapshot_baseline() -> Dict[str, str]:
    """Сделать снимок baseline-хешей. Вызывается один раз (at test start)."""
    global _BASELINE_HASHES
    _BASELINE_HASHES = compute_baseline_hashes()
    return _BASELINE_HASHES.copy()


def assert_baseline_intact(
    saved_hashes: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Проверить что baseline-файлы не модифицированы.

    saved_hashes: хеши из snapshot_baseline(). Если None — берём кэш.
    Возвращает список ошибок (пустой = OK).
    """
    expected = saved_hashes or _BASELINE_HASHES
    if expected is None:
        return ["No baseline snapshot: call snapshot_baseline() first"]

    current = compute_baseline_hashes()
    errors: List[str] = []

    for path, old_hash in expected.items():
        new_hash = current.get(path, "MISSING")
        if old_hash != new_hash:
            errors.append(f"Baseline modified: {path}")

    return errors


# ─── Combined guard ───────────────────────────────────────────────────

def run_full_guard() -> Dict[str, Any]:
    """Запустить все проверки guard: broker-импорты + baseline intact.

    Возвращает dict:
      {
          "broker_check": {"ok": bool, "errors": [...]},
          "baseline_check": {"ok": bool, "errors": [...]},
          "overall_ok": bool,
      }
    """
    broker_errors = check_all_fusion_files()
    baseline_errors = assert_baseline_intact()

    return {
        "broker_check": {
            "ok": len(broker_errors) == 0,
            "errors": broker_errors,
        },
        "baseline_check": {
            "ok": len(baseline_errors) == 0,
            "errors": baseline_errors,
        },
        "overall_ok": len(broker_errors) == 0 and len(baseline_errors) == 0,
    }
