"""Risk/Allocator scorecard: сухой прогон проверок acceptans criteria.

Проверяет:
1. max_live_slots ≤ 3
2. 1 contract max per entry
3. RI excluded
4. portfolio deduplication
5. signal_pool non-empty (если есть registry)

Вычисляет composite score:
  slots_score = 1.0 - (max(0, n_slots - max_slots) / max_slots)
  contracts_score = 1.0 если все contracts ≤1, иначе 0.0
  exclusion_score = 1.0 если RI нет, 0.0 если RI есть
  composite = (slots_score + contracts_score + exclusion_score) / 3

Выводит JSON-отчёт {pass, metrics, composite_score}.

Источник: analysis.md (criteria mapping), config.json risk params.
"""
import json
import sys
from pathlib import Path

COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
STATE_DIR = COMBINE_DIR / "state"
CONFIG_FILE = COMBINE_DIR / "config.json"


def load_config() -> dict:
    """Загружает config.json."""
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def check_max_slots(portfolio: dict, max_slots: int = 3) -> dict:
    """Проверка: n_slots ≤ max_slots."""
    n = len(portfolio.get("slots", {}))
    return {
        "name": "max_live_slots",
        "passed": n <= max_slots,
        "actual": n,
        "limit": max_slots,
        "detail": "n_slots=%d, max=%d" % (n, max_slots),
    }


def check_contracts_per_entry(portfolio: dict, max_contracts: int = 1) -> dict:
    """Проверка: все slots имеют contracts ≤ max_contracts."""
    violations = []
    for sid, slot in portfolio.get("slots", {}).items():
        c = slot.get("contracts", 1)
        if c > max_contracts:
            violations.append("%s(contracts=%d)" % (sid, c))
    return {
        "name": "max_contracts_per_entry",
        "passed": len(violations) == 0,
        "violations": violations,
        "limit": max_contracts,
        "detail": "violations=%s" % violations if violations else "all OK",
    }


def check_ri_excluded(portfolio: dict, excluded: list[str]) -> dict:
    """Проверка: нет слотов с ticker в excluded."""
    ri_slots = [
        sid for sid, s in portfolio.get("slots", {}).items()
        if s.get("ticker") in excluded
    ]
    return {
        "name": "ri_excluded",
        "passed": len(ri_slots) == 0,
        "ri_slots": ri_slots,
        "excluded_list": excluded,
        "detail": "ri_slots=%s" % ri_slots if ri_slots else "OK, no excluded tickers",
    }


def check_no_duplicates(portfolio: dict) -> dict:
    """Проверка: нет дублей (ticker, strategy)."""
    seen = {}
    duplicates = []
    for sid, slot in portfolio.get("slots", {}).items():
        key = (slot.get("ticker", ""), slot.get("strategy", ""))
        if key in seen:
            duplicates.append("%s==%s (%s)" % (sid, seen[key], key))
        else:
            seen[key] = sid
    return {
        "name": "no_duplicates",
        "passed": len(duplicates) == 0,
        "duplicates": duplicates,
        "detail": "duplicates=%s" % duplicates if duplicates else "OK",
    }


def check_signal_pool(pool_path: Path) -> dict:
    """Проверка: signal_pool.json не пустой."""
    if not pool_path.exists():
        return {
            "name": "signal_pool_non_empty",
            "passed": False,
            "detail": "signal_pool.json not found",
        }
    data = json.loads(pool_path.read_text())
    n = len(data.get("strategies", {}))
    return {
        "name": "signal_pool_non_empty",
        "passed": n > 0,
        "n_strategies": n,
        "detail": "n_strategies=%d" % n,
    }


def compute_composite_score(checks: list[dict]) -> float:
    """Composite score: среднее по всем check's passed (1.0/0.0)."""
    if not checks:
        return 0.0
    scores = [1.0 if c["passed"] else 0.0 for c in checks]
    return round(sum(scores) / len(scores), 3)


def run_scorecard(
    portfolio_path: str | Path | None = None,
    config_path: str | Path | None = None,
    pool_path: str | Path | None = None,
) -> dict:
    """Запуск полного scorecard. Возвращает JSON-отчёт."""
    config = load_config()
    risk = config.get("risk", {})
    excluded = config.get("excluded", ["RI"])
    max_slots = risk.get("max_slots", 3)
    max_contracts = risk.get("max_contracts_per_entry", 1)

    portfolio_path = Path(portfolio_path or STATE_DIR / "portfolio.json")
    pool_path = Path(pool_path or STATE_DIR / "signal_pool.json")

    if not portfolio_path.exists():
        return {
            "pass": False,
            "composite_score": 0.0,
            "metrics": [],
            "error": "portfolio.json not found",
        }

    portfolio = json.loads(portfolio_path.read_text())

    checks = [
        check_max_slots(portfolio, max_slots),
        check_contracts_per_entry(portfolio, max_contracts),
        check_ri_excluded(portfolio, excluded),
        check_no_duplicates(portfolio),
        check_signal_pool(pool_path),
    ]

    composite = compute_composite_score(checks)
    all_passed = all(c["passed"] for c in checks)

    return {
        "pass": all_passed,
        "composite_score": composite,
        "metrics": checks,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    """Запуск scorecard из CLI. Печатает JSON-отчёт."""
    result = run_scorecard()
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # Итоговая строка
    status = "PASS" if result["pass"] else "FAIL"
    print("\nSCORECARD: %s (composite=%.3f)" % (status, result["composite_score"]))

    # Детали по каждому check
    for m in result.get("metrics", []):
        mark = "OK" if m["passed"] else "FAIL"
        print("  [%s] %s: %s" % (mark, m["name"], m["detail"]))


if __name__ == "__main__":
    main()
