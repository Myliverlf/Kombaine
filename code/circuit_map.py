"""Circuit Map — карта контура strategy_combine + дублей EXCLUDED_TICKERS + конфиг-валидация.

Фича 1 плана аттестации:
  1. Сканирует файловую систему code/ — строит граф imports-зависимостей модулей.
  2. Централизованный EXCLUDED_TICKERS: проверяет что все дубли в модулях
     совпадают с единым источником (R5 — exposureочищает).
  3. Детектит наличие state/strategy_registry.json, config.json полей
     mode / paper_first (R1, R2, R3 — detection).
  4. Выводит JSON-отчёт: {modules, sync_points, exclusions, risks_detected}.

Не ходит в сеть, не импортирует broker/client, не пишет state/.
"""
from __future__ import annotations

import ast as _ast
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Canonical EXCLUDED_TICKERS — единый источник правды
CANONICAL_EXCLUDED: Set[str] = {"RI"}

# Known sync points (entry-point functions that connect modules)
SYNC_POINTS = [
    {
        "name": "pipeline_ranker.run_pipeline",
        "file": "pipeline_ranker.py",
        "role": "analytics→rank→select→scorecard",
    },
    {
        "name": "fusion_pipeline.run_fusion_pipeline",
        "file": "fusion_pipeline.py",
        "role": "signal_fusion → scoring → top selection",
    },
    {
        "name": "risk_timesfm_bridge.compute_forecast_risk_adjustments",
        "file": "risk_timesfm_bridge.py",
        "role": "forecast→risk adjustments",
    },
    {
        "name": "forecast_context_scorer (4 bridges)",
        "file": "forecast_context_scorer.py",
        "role": "ForecastContext → allocator/quality_gate/risk/signal_fusion",
    },
    {
        "name": "strategy_supervisor_flow",
        "file": "strategy_supervisor_flow.py",
        "role": "registry → legacy views (waitlist.json, signal_pool.json)",
    },
    {
        "name": "core/engine.py runtime loop",
        "file": "core/engine.py",
        "role": "fetch_candles → build_signal → approve_entry → post → record",
    },
]


def scan_modules(code_dir: str) -> List[Dict[str, Any]]:
    """Сканирует .py файлы в code/ — извлекает imports, AST-guard, EXCLUDED_TICKERS."""
    modules = []
    if not os.path.isdir(code_dir):
        return modules

    for fname in sorted(os.listdir(code_dir)):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(code_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                source = fh.read()
        except (OSError, UnicodeDecodeError):
            continue

        # Parse imports
        try:
            tree = _ast.parse(source, filename=fname)
        except SyntaxError:
            modules.append({
                "file": fname,
                "parse_error": True,
                "imports": [],
                "has_ast_guard": False,
                "excluded_tickers_value": None,
            })
            continue

        imports = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        # Check AST-guard pattern
        has_ast_guard = "check_no_broker_imports" in source or "assert_no_broker_imports" in source

        # Check EXCLUDED_TICKERS definition
        excluded_value = None
        for line in source.split("\n"):
            m = re.match(r'^EXCLUDED_TICKERS\s*=\s*(\{.*\})', line)
            if m:
                try:
                    excluded_value = _ast.literal_eval(m.group(1))
                except (SyntaxError, NameError, TypeError, ValueError):
                    excluded_value = m.group(1)
            # Also check RI_EXCLUDED alias
            m2 = re.match(r'^RI_EXCLUDED\s*=\s*(\{.*\})', line)
            if m2:
                try:
                    excluded_value = _ast.literal_eval(m2.group(1))
                except (SyntaxError, NameError, TypeError, ValueError):
                    excluded_value = m2.group(1)

        modules.append({
            "file": fname,
            "parse_error": False,
            "imports": imports,
            "has_ast_guard": has_ast_guard,
            "excluded_tickers_value": excluded_value,
        })

    return modules


def check_excluded_consistency(modules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Проверяет что все EXCLUDED_TICKERS в модулях совпадают с CANONICAL_EXCLUDED."""
    mismatches = []
    matches = []
    for m in modules:
        val = m.get("excluded_tickers_value")
        if val is not None:
            if isinstance(val, set) and val == CANONICAL_EXCLUDED:
                matches.append(m["file"])
            elif isinstance(val, str) and val == str(CANONICAL_EXCLUDED):
                matches.append(m["file"])
            else:
                mismatches.append({
                    "file": m["file"],
                    "value": val,
                    "expected": list(CANONICAL_EXCLUDED),
                })

    return {
        "canonical": list(CANONICAL_EXCLUDED),
        "matches": matches,
        "mismatches": mismatches,
        "total_declarations": len(matches) + len(mismatches),
        "consistent": len(mismatches) == 0,
    }


def check_config(config_path: str) -> Dict[str, Any]:
    """Детектит R1 (mode=live), R2 (paper_first=false) из config.json."""
    risks = []
    config_data = None

    if not os.path.isfile(config_path):
        risks.append({
            "id": "R0",
            "severity": "HIGH",
            "detail": f"config.json not found at {config_path}",
        })
        return {"exists": False, "config": None, "risks": risks}

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config_data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        risks.append({
            "id": "R0",
            "severity": "HIGH",
            "detail": f"config.json parse error: {exc}",
        })
        return {"exists": True, "config": None, "risks": risks}

    # R1: mode=live
    mode = config_data.get("mode", "unknown")
    if mode == "live":
        risks.append({
            "id": "R1",
            "severity": "HIGH",
            "detail": 'config.json mode="live" — engine.py может отправить реальные ордера',
        })

    # R2: paper_first=false
    paper_first = config_data.get("paper_first", True)
    if paper_first is False:
        risks.append({
            "id": "R2",
            "severity": "MEDIUM",
            "detail": "config.json paper_first=false — противоречит ARCHITECTURE.md §2.7",
        })

    return {
        "exists": True,
        "config": {
            "mode": mode,
            "paper_first": paper_first,
            "excluded": config_data.get("excluded", []),
            "deposit_rub": config_data.get("deposit_rub"),
            "universe": config_data.get("universe", []),
        },
        "risks": risks,
    }


def check_state_files(state_dir: str) -> Dict[str, Any]:
    """Детектит R3 (strategy_registry.json отсутствует) + проверяет state файлы."""
    risks = []
    files_status = {}

    registry_path = os.path.join(state_dir, "strategy_registry.json")
    if not os.path.isfile(registry_path):
        risks.append({
            "id": "R3",
            "severity": "MEDIUM",
            "detail": "state/strategy_registry.json отсутствует — supervisor flow может не работать",
        })
        files_status["strategy_registry.json"] = "MISSING"
    else:
        files_status["strategy_registry.json"] = "OK"

    for fname in ["waitlist.json", "signal_pool.json", "portfolio.json", "regime_snapshot.json"]:
        fpath = os.path.join(state_dir, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                files_status[fname] = {"status": "OK", "type": type(data).__name__}
            except (json.JSONDecodeError, OSError):
                files_status[fname] = {"status": "PARSE_ERROR"}
        else:
            files_status[fname] = "MISSING"

    return {"files": files_status, "risks": risks}


def build_import_graph(modules: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Строит граф imports: модуль → список импортируемых модулей (из code/)."""
    code_modules = {m["file"].replace(".py", "") for m in modules}
    graph = {}
    for m in modules:
        fname = m["file"].replace(".py", "")
        deps = []
        for imp in m.get("imports", []):
            base = imp.split(".")[-1]  # last component
            if base in code_modules and base != fname:
                deps.append(base)
        graph[fname] = sorted(set(deps))
    return graph


def run_full_scan(
    code_dir: Optional[str] = None,
    config_path: Optional[str] = None,
    state_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Полный скан контура — возвращает JSON-отчёт."""
    if code_dir is None:
        code_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "code")
    if config_path is None:
        project_root = os.path.dirname(code_dir)
        config_path = os.path.join(project_root, "config.json")
    if state_dir is None:
        project_root = os.path.dirname(code_dir)
        state_dir = os.path.join(project_root, "state")

    # 1. Scan modules
    modules = scan_modules(code_dir)

    # 2. Import graph
    import_graph = build_import_graph(modules)

    # 3. EXCLUDED_TICKERS consistency
    exclusion_check = check_excluded_consistency(modules)

    # 4. Config validation
    config_check = check_config(config_path)

    # 5. State files
    state_check = check_state_files(state_dir)

    # 6. Collect all risks
    all_risks = config_check["risks"] + state_check["risks"]
    if not exclusion_check["consistent"]:
        all_risks.append({
            "id": "R5",
            "severity": "LOW",
            "detail": (
                "EXCLUDED_TICKERS diverges across modules: "
                f"{[m['file'] for m in modules if m.get('excluded_tickers_value') is not None]}"
            ),
            "mismatches": exclusion_check["mismatches"],
        })

    # 7. Summary
    n_modules = len(modules)
    n_with_guard = sum(1 for m in modules if m.get("has_ast_guard"))
    n_parse_errors = sum(1 for m in modules if m.get("parse_error"))
    n_excluded_decl = exclusion_check["total_declarations"]

    report = {
        "summary": {
            "n_modules": n_modules,
            "n_with_ast_guard": n_with_guard,
            "n_parse_errors": n_parse_errors,
            "n_excluded_declarations": n_excluded_decl,
            "excluded_consistent": exclusion_check["consistent"],
            "n_sync_points": len(SYNC_POINTS),
            "n_risks": len(all_risks),
        },
        "modules": [
            {
                "file": m["file"],
                "has_ast_guard": m.get("has_ast_guard", False),
                "excluded_tickers": m.get("excluded_tickers_value"),
                "parse_error": m.get("parse_error", False),
            }
            for m in modules
        ],
        "import_graph": import_graph,
        "sync_points": SYNC_POINTS,
        "exclusions": exclusion_check,
        "config": config_check,
        "state": state_check,
        "risks_detected": all_risks,
    }

    return report


def _main() -> None:
    """CLI: сканирует текущий проект и выводит JSON-отчёт."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code_dir = os.path.join(project_root, "code")
    config_path = os.path.join(project_root, "config.json")
    state_dir = os.path.join(project_root, "state")

    # If running from workspace, find the real project
    if not os.path.isdir(code_dir):
        alt_root = "/root/prop-desk/strategy_combine"
        if os.path.isdir(os.path.join(alt_root, "code")):
            project_root = alt_root
            code_dir = os.path.join(project_root, "code")
            config_path = os.path.join(project_root, "config.json")
            state_dir = os.path.join(project_root, "state")

    report = run_full_scan(code_dir, config_path, state_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    # Print summary
    s = report["summary"]
    print("\n=== Circuit Map Summary ===")
    print(f"Modules: {s['n_modules']} (AST-guard: {s['n_with_ast_guard']}, parse errors: {s['n_parse_errors']})")
    print(f"EXCLUDED_TICKERS declarations: {s['n_excluded_declarations']}, consistent: {s['excluded_consistent']}")
    print(f"Sync points: {s['n_sync_points']}")
    print(f"Risks detected: {s['n_risks']}")
    for risk in report["risks_detected"]:
        print(f"  [{risk['severity']}] {risk['id']}: {risk['detail']}")


if __name__ == "__main__":
    _main()
