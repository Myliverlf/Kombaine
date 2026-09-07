"""Live Order Guard — safety checks to prevent live broker mutations.

Проверяет:
  1. assert_no_broker_imports(code_dir): grep на tinkoff/broker/order в code/
  2. assert_paper_mode(config_dict): mode == "paper"
  3. assert_no_live_mutations(portfolio_path, checksum_before): verify checksum
  4. generate_audit_report(results): JSON-отчёт portfolio_audit_report.json

Не вызывает broker. Не пишет в state/ при dry-run.

Источники:
  - plan.md Фича 4
  - analysis.md §3.1 fact #2 (config.json mode="live" — risk R1)
  - analysis.md §3.1 fact #3 (paper_first=false — risk R2)
"""
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Patterns that indicate broker/live-order code
BROKER_PATTERNS = [
    r"\btinkoff\b",
    r"\bbroker\b",
    r"\border\b",
    r"\bexecute_order\b",
    r"\bsend_order\b",
    r"\bplace_order\b",
    r"\bbuy_shares?\b",
    r"\bsell_shares?\b",
    r"\bopen_position\b",  # only in code/ — we check this is state-only
    r"\bTinkoffInvestments\b",
    r"\bfrom\s+tinkoff",
    r"\bimport\s+tinkoff",
]

# Compile once for performance
_BROKER_RE = re.compile("|".join(BROKER_PATTERNS), re.IGNORECASE)

# Files to skip (known-safe patterns)
SKIP_FILES = {
    "__pycache__",
    ".git",
    "*.pyc",
}

# Known false-positive patterns in our codebase
FALSE_POSITIVE_CONTEXTS = [
    "open_position",  # This is a portfolio state field, not a broker call
]


def assert_no_broker_imports(
    code_dir: str | Path,
    exclude_patterns: Optional[Set[str]] = None,
) -> dict:
    """Scan code_dir for broker-related imports/calls.

    Args:
        code_dir: directory to scan (typically code/ or core/)
        exclude_patterns: filenames to exclude from scan

    Returns:
        {"ok": bool, "violations": [{"file": str, "line": int, "text": str}, ...]}
    """
    code_dir = Path(code_dir)
    exclude = exclude_patterns or set()
    violations: List[Dict[str, Any]] = []

    if not code_dir.exists():
        return {"ok": True, "violations": [], "error": f"dir not found: {code_dir}"}

    for py_file in sorted(code_dir.rglob("*.py")):
        if py_file.name in exclude or "__pycache__" in str(py_file):
            continue

        try:
            lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith("#"):
                continue
            # Skip strings that are just docstrings about portfolio state
            if any(fp in stripped for fp in FALSE_POSITIVE_CONTEXTS):
                # Allow "open_position" as a dict key reference but flag "broker" etc
                if not any(
                    p in stripped.lower()
                    for p in ["broker", "tinkoff", "order(", "send_order", "place_order"]
                ):
                    continue
            if _BROKER_RE.search(stripped):
                violations.append({
                    "file": str(py_file),
                    "line": i,
                    "text": stripped[:120],
                })

    return {"ok": len(violations) == 0, "violations": violations}


def assert_paper_mode(config: dict) -> dict:
    """Verify config enforces paper mode.

    Args:
        config: config dict (from config.json)

    Returns:
        {"ok": bool, "mode": str, "paper_first": bool, "issues": [str]}
    """
    issues: List[str] = []
    mode = config.get("mode", "")
    paper_first = config.get("paper_first", False)

    if mode != "paper":
        issues.append(f"mode={mode!r} (expected 'paper')")
    if not paper_first:
        issues.append(f"paper_first={paper_first} (expected True)")

    return {
        "ok": len(issues) == 0,
        "mode": mode,
        "paper_first": paper_first,
        "issues": issues,
    }


def file_checksum(path: str | Path) -> str:
    """Compute SHA-256 checksum of a file."""
    path = Path(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_no_live_mutations(
    portfolio_path: str | Path,
    checksum_before: str,
) -> dict:
    """Verify portfolio.json hasn't changed since checksum was computed.

    Args:
        portfolio_path: path to portfolio.json
        checksum_before: SHA-256 checksum computed before enforcement

    Returns:
        {"ok": bool, "checksum_before": str, "checksum_after": str, "changed": bool}
    """
    portfolio_path = Path(portfolio_path)
    if not portfolio_path.exists():
        return {
            "ok": False,
            "checksum_before": checksum_before,
            "checksum_after": "",
            "error": "portfolio.json not found",
            "changed": True,
        }

    checksum_after = file_checksum(portfolio_path)
    changed = checksum_before != checksum_after

    return {
        "ok": not changed,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "changed": changed,
    }


def generate_audit_report(
    broker_check: dict,
    paper_mode: dict,
    mutation_check: Optional[dict] = None,
    portfolio_check: Optional[dict] = None,
) -> dict:
    """Generate a comprehensive audit report.

    Args:
        broker_check: result from assert_no_broker_imports
        paper_mode: result from assert_paper_mode
        mutation_check: result from assert_no_live_mutations (optional)
        portfolio_check: result from validate_portfolio (optional)

    Returns:
        Full audit report dict.
    """
    all_ok = broker_check.get("ok", False) and paper_mode.get("ok", False)

    if mutation_check is not None:
        all_ok = all_ok and mutation_check.get("ok", False)

    checks = {
        "no_broker_imports": broker_check,
        "paper_mode": paper_mode,
    }

    if mutation_check is not None:
        checks["no_live_mutations"] = mutation_check

    if portfolio_check is not None:
        checks["portfolio_validation"] = portfolio_check
        all_ok = all_ok and portfolio_check.get("verdict") == "PASS"

    return {
        "verdict": "SAFE" if all_ok else "UNSAFE",
        "checks": checks,
        "summary": _build_summary(checks),
    }


def _build_summary(checks: dict) -> str:
    """Build human-readable summary from checks."""
    parts = []
    for name, check in checks.items():
        ok = check.get("ok", check.get("verdict") == "PASS")
        status = "SAFE" if ok else "UNSAFE"
        parts.append(f"{name}: {status}")
    return "; ".join(parts)


# ── Self-test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    code_dir = sys.argv[1] if len(sys.argv) > 1 else "/root/prop-desk/strategy_combine/code"
    config_path = sys.argv[2] if len(sys.argv) > 2 else "/root/prop-desk/strategy_combine/config.json"

    print("=== Live Order Guard ===")

    # Check 1: broker imports
    broker_result = assert_no_broker_imports(code_dir)
    status = "SAFE" if broker_result["ok"] else "UNSAFE"
    print(f"\n[{status}] Broker imports in code/: "
          f"{len(broker_result['violations'])} violations")
    for v in broker_result["violations"][:5]:
        print(f"  {v['file']}:{v['line']}: {v['text']}")

    # Check 2: paper mode
    config_path = Path(config_path)
    if config_path.exists():
        config = json.loads(config_path.read_text())
        paper_result = assert_paper_mode(config)
        status = "SAFE" if paper_result["ok"] else "UNSAFE"
        print(f"\n[{status}] Paper mode: mode={paper_result['mode']}, "
              f"paper_first={paper_result['paper_first']}")
        for issue in paper_result["issues"]:
            print(f"  - {issue}")
    else:
        print(f"\nConfig not found: {config_path}")

    print("\nDone")
