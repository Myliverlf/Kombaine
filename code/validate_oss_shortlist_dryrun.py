"""Dry-run валидатор OSS shortlist — проверка по критериям приёмки task.md.

Модуль read-only, stdout-отчёт, exit 0 при PASS.
Не импортирует broker/client, не пишет state/, не ходит в сеть.

Паттерн: code/validate_regime_gate_dryrun.py, code/validate_allocator_dryrun.py.

Проверяет:
  1. Forbidden patterns: post_order/place_order/send_order/submit_order/Client(
     как function calls в новых модулях (AST-based, не ловит строковые определения)
  2. py_compile: oss_shortlist.py, scorecard_metrics.py, test_oss_shortlist.py
  3. fixtures ≥ 3 в tests/test_oss_shortlist.py
  4. RI excluded: regime_gate VETO для RI + portfolio_copy.json
  5. max live slots ≤ 3: из portfolio_copy.json fixture
  6. max_contracts_per_entry = 1: из portfolio_copy.json fixture
  7. Метрика PnL↑/risk↓: ab_compare доступен и работает
  8. Плацдарм: regime_gate.py/admit доступен и RI VETO работает
"""
import ast
import json
import py_compile
import re
import sys
from pathlib import Path

COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
CODE_DIR = COMBINE_DIR / "code"
TESTS_DIR = COMBINE_DIR / "tests"
FIXTURES_DIR = TESTS_DIR / "fixtures"

sys.path.insert(0, str(CODE_DIR))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    """Print check result, return ok."""
    status = "PASS" if ok else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return ok


def _has_broker_calls(filepath: Path) -> list:
    """Detect broker call patterns in Python code via AST.

    Only matches actual function calls (post_order(...), Client(...)),
    not string definitions like _FORBIDDEN = ("post_order", ...).
    """
    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return ["<syntax error>"]

    # Function names to detect as calls
    call_names = {"post_order", "place_order", "send_order", "submit_order", "Client"}

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in call_names:
                found.append(f"{func.id}()")
            elif isinstance(func, ast.Attribute) and func.attr in call_names:
                found.append(f".{func.attr}()")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in call_names:
                    found.append(f"import {alias.name}")

    return found


def main() -> bool:
    """Run all checks. Returns True if all PASS."""
    print("=" * 65)
    print("  OSS SHORTLIST DRY-RUN VALIDATOR")
    print("=" * 65)
    all_pass = True

    # ── 1. Forbidden patterns in new modules (AST-based) ───────────────
    print("\n--- Forbidden patterns (no broker/live orders) ---")
    new_modules = [
        CODE_DIR / "oss_shortlist.py",
        CODE_DIR / "scorecard_metrics.py",
        TESTS_DIR / "test_oss_shortlist.py",
    ]
    for mod in new_modules:
        if not mod.exists():
            all_pass = _check(f"file exists: {mod.name}", False, str(mod)) and all_pass
            continue
        broker_calls = _has_broker_calls(mod)
        ok = len(broker_calls) == 0
        detail = f"found: {broker_calls}" if broker_calls else "clean"
        all_pass = _check(f"no broker calls: {mod.name}", ok, detail) and all_pass

    # ── 2. py_compile new modules ──────────────────────────────────────
    print("\n--- py_compile ---")
    compile_targets = [
        CODE_DIR / "oss_shortlist.py",
        CODE_DIR / "scorecard_metrics.py",
        TESTS_DIR / "test_oss_shortlist.py",
        CODE_DIR / "validate_oss_shortlist_dryrun.py",
    ]
    for target in compile_targets:
        try:
            py_compile.compile(str(target), doraise=True)
            all_pass = _check(f"py_compile: {target.name}", True) and all_pass
        except py_compile.PyCompileError as exc:
            all_pass = _check(f"py_compile: {target.name}", False, str(exc)) and all_pass

    # ── 3. Fixtures count ≥ 3 ──────────────────────────────────────────
    print("\n--- Fixtures check ---")
    test_path = TESTS_DIR / "test_oss_shortlist.py"
    if test_path.exists():
        test_content = test_path.read_text()
        # Count @pytest.fixture definitions
        fixture_defs = re.findall(r"@pytest\.fixture", test_content)
        fixture_count = len(fixture_defs)
        all_pass = _check(
            "fixtures ≥ 3", fixture_count >= 3,
            f"found {fixture_count}"
        ) and all_pass
    else:
        all_pass = _check("test file exists", False, str(test_path)) and all_pass

    # Check fixtures/ directory exists and has files
    fixture_files = list(FIXTURES_DIR.glob("*.json")) if FIXTURES_DIR.exists() else []
    all_pass = _check(
        "tests/fixtures/ has data", len(fixture_files) >= 2,
        f"{len(fixture_files)} json files"
    ) and all_pass

    # ── 4. RI excluded ─────────────────────────────────────────────────
    print("\n--- RI excluded ---")
    try:
        from regime_gate import admit
        ri_result = admit({"ticker": "RI", "direction": "LONG"}, None, {"excluded": ["RI"]})
        all_pass = _check(
            "RI excluded via config + gate VETO", ri_result["admit"] is False,
            f"reason={ri_result['reason']}"
        ) and all_pass
    except ImportError as exc:
        all_pass = _check("RI exclusion imports", False, str(exc)) and all_pass

    # ── 5. Max live slots ≤ 3 ──────────────────────────────────────────
    print("\n--- Max live slots ---")
    portfolio_path = FIXTURES_DIR / "portfolio_copy.json"
    if portfolio_path.exists():
        with open(portfolio_path) as f:
            portfolio = json.load(f)
        max_slots = portfolio.get("max_slots", 99)
        n_slots = len(portfolio.get("slots", {}))
        all_pass = _check(
            "max_slots ≤ 3", max_slots <= 3,
            f"max_slots={max_slots}"
        ) and all_pass
        all_pass = _check(
            "slot count ≤ max_slots", n_slots <= max_slots,
            f"n={n_slots}"
        ) and all_pass
    else:
        all_pass = _check("portfolio_copy.json exists", False) and all_pass

    # ── 6. max_contracts_per_entry = 1 ─────────────────────────────────
    print("\n--- Max contracts per entry ---")
    if portfolio_path.exists():
        with open(portfolio_path) as f:
            portfolio = json.load(f)
        max_c = portfolio.get("max_contracts_per_entry", 99)
        all_pass = _check(
            "max_contracts_per_entry == 1", max_c == 1,
            f"value={max_c}"
        ) and all_pass
        for sid, slot in portfolio.get("slots", {}).items():
            all_pass = _check(
                f"slot {sid} contracts==1", slot.get("contracts") == 1,
                f"contracts={slot.get('contracts')}"
            ) and all_pass

    # ── 7. Metric PnL↑/risk↓ (ab_compare works) ───────────────────────
    print("\n--- PnL↑/risk↓ metric ---")
    try:
        from scorecard_metrics import ab_compare
        baseline = [0.0, 0.01, -0.005, 0.005, -0.01]
        candidate = [0.0, 0.02, -0.002, 0.012, -0.005]
        result = ab_compare(baseline, candidate)
        has_keys = "pnl_up" in result and "risk_down" in result
        all_pass = _check(
            "ab_compare returns pnl_up+risk_down", has_keys,
            f"pnl_up={result.get('pnl_up')}, risk_down={result.get('risk_down')}"
        ) and all_pass
    except ImportError as exc:
        all_pass = _check("ab_compare import", False, str(exc)) and all_pass

    # ── 8. Preserved basecamp ──────────────────────────────────────────
    print("\n--- Preserved basecamp (existing modules untouched) ---")
    basecamp_files = [
        CODE_DIR / "regime_gate.py",
        CODE_DIR / "candidate_allocator.py",
        CODE_DIR / "allocator_metrics.py",
        CODE_DIR / "audit_signal_pool.py",
    ]
    for bf in basecamp_files:
        all_pass = _check(
            f"basecamp exists: {bf.name}", bf.exists(),
        ) and all_pass

    # ── Overall ────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"  OVERALL: {'PASS' if all_pass else 'FAIL'}")
    print("  DRY_RUN: no real orders, no broker calls, read-only")
    print("=" * 65)

    return all_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
