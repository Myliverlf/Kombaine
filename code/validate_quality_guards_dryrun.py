"""Validate Quality Guards — CLI dry-run validator для overfit/sensitivity/degradation.

Модуль НЕ импортирует broker/client, НЕ пишет state/, НЕ ходит в сеть.
Проверяет все компоненты quality guards через synthetic data.

Запуск: python validate_quality_guards_dryrun.py
"""
import json
import sys
import traceback
from pathlib import Path

# Ensure code/ is on sys.path
CODE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE_DIR))

from overfit_guard import (
    deflated_sharpe_ratio,
    sharpe_degradation_ratio,
    min_oos_trades_ok,
    embargo_gap_ok,
    is_overfit,
    overfit_guard_report,
)
from sensitivity_analyzer import (
    oat_sensitivity,
    sensitivity_report,
    edge_case_explorer,
)
from degradation_detector import (
    cusum_detect,
    rolling_ir,
    health_score,
    degradation_alert,
    degradation_report,
    ALL_STATUSES,
)


def main():
    results = {"checks": [], "passed": 0, "failed": 0}

    def check(name, fn):
        try:
            result = fn()
            results["checks"].append({"name": name, "status": "PASS", "detail": str(result)[:200]})
            results["passed"] += 1
            print(f"  [PASS] {name}")
        except Exception as e:
            results["checks"].append({"name": name, "status": "FAIL", "detail": str(e)})
            results["failed"] += 1
            print(f"  [FAIL] {name}: {e}")

    # ─── Overfit Guard ──────────────────────────────────────────────────
    print("\n=== Overfit Guard ===")

    def check_dsr():
        dsr = deflated_sharpe_ratio(2.0, num_trials=100, num_obs=250)
        assert 0.0 <= dsr <= 1.0, f"DSR out of range: {dsr}"
        return f"DSR(2.0, 100, 250) = {dsr:.4f}"

    check("deflated_sharpe_ratio", check_dsr)

    def check_degradation_ratio():
        deg = sharpe_degradation_ratio(2.0, 1.5)
        assert abs(deg - 0.75) < 0.01, f"Expected 0.75, got {deg}"
        return f"degradation(2.0, 1.5) = {deg}"

    check("sharpe_degradation_ratio", check_degradation_ratio)

    def check_min_trades():
        assert min_oos_trades_ok(20) is True
        assert min_oos_trades_ok(5) is False
        return "min_oos_trades_ok(20)=True, ok(5)=False"

    check("min_oos_trades_ok", check_min_trades)

    def check_embargo():
        assert embargo_gap_ok([5, 10, 7]) is True
        assert embargo_gap_ok([2, 10]) is False
        return "embargo_gap_ok OK"

    check("embargo_gap_ok", check_embargo)

    def check_is_overfit_pass():
        r = is_overfit(2.0, 1.8, num_trials=10, num_obs=250, oos_trades=30)
        assert r["is_overfit"] is False, f"Should not be overfit: {r}"
        return f"is_overfit={r['is_overfit']}, dsr={r['dsr']:.3f}"

    check("is_overfit (PASS case)", check_is_overfit_pass)

    def check_is_overfit_fail():
        r = is_overfit(2.0, 0.1, num_trials=1000, num_obs=50, oos_trades=3)
        assert r["is_overfit"] is True, f"Should be overfit: {r}"
        return f"is_overfit={r['is_overfit']}, reasons={r['reasons']}"

    check("is_overfit (FAIL case)", check_is_overfit_fail)

    def check_guard_report():
        windows = [
            {"is_sharpe": 1.5, "oos_sharpe": 1.3, "oos_trades": 20},
            {"is_sharpe": 1.8, "oos_sharpe": 1.5, "oos_trades": 25},
        ]
        report = overfit_guard_report(windows, num_trials=10)
        assert report["verdict"] == "PASS"
        return f"verdict={report['verdict']}, avg_dsr={report['avg_dsr']:.3f}"

    check("overfit_guard_report", check_guard_report)

    # ─── Sensitivity Analyzer ───────────────────────────────────────────
    print("\n=== Sensitivity Analyzer ===")

    def check_oat():
        base = {"expectancy": 40, "risk": 35, "regime": 25}
        score_fn = lambda w: sum(v**2 for v in w.values())
        result = oat_sensitivity(base, score_fn)
        for key in base:
            assert "sensitivity_index" in result[key]
            assert "monotonic" in result[key]
        return f"OAT keys: {list(result.keys())}"

    check("oat_sensitivity", check_oat)

    def check_report():
        base = {"expectancy": 40, "risk": 35, "regime": 25}
        score_fn = lambda w: sum(v**2 for v in w.values())
        report = sensitivity_report(base, score_fn)
        assert "most_sensitive" in report
        assert len(report["ranked"]) == 3
        return f"most_sensitive={report['most_sensitive']}, monotonic={report['all_monotonic']}"

    check("sensitivity_report", check_report)

    def check_edge_cases():
        base = {"expectancy": 40, "risk": 35, "regime": 25}
        score_fn = lambda w: sum(w.values())
        cases = edge_case_explorer(base, score_fn)
        assert len(cases) >= 5
        return f"edge cases: {len(cases)}"

    check("edge_case_explorer", check_edge_cases)

    # ─── Degradation Detector ───────────────────────────────────────────
    print("\n=== Degradation Detector ===")

    def check_cusum():
        series = [1.0] * 10 + [-3.0] * 10
        cps = cusum_detect(series, threshold=3.0, drift=0.3)
        assert len(cps) >= 1
        return f"CUSUM changepoints: {cps}"

    check("cusum_detect", check_cusum)

    def check_rolling_ir():
        returns = [0.01, 0.02, 0.01, -0.01, 0.015]
        ir = rolling_ir(returns, window=3)
        assert len(ir) == 5
        non_none = [v for v in ir if v is not None]
        assert len(non_none) >= 3
        return f"rolling IR values: {[round(v, 3) if v else None for v in ir]}"

    check("rolling_ir", check_rolling_ir)

    def check_health_healthy():
        h = health_score(1.5, 0.5, -0.05, 0.55)
        assert h["status"] == "healthy"
        return f"status={h['status']}, composite={h['composite_score']}"

    check("health_score (healthy)", check_health_healthy)

    def check_health_dead():
        h = health_score(-1.0, -0.5, -0.40, 0.20)
        assert h["status"] == "dead"
        return f"status={h['status']}, composite={h['composite_score']}"

    check("health_score (dead)", check_health_dead)

    def check_alert():
        h = health_score(-1.0, -0.5, -0.40, 0.20)
        a = degradation_alert(h)
        assert a["alert"] is True
        assert a["severity"] == "critical"
        return f"alert={a['alert']}, severity={a['severity']}"

    check("degradation_alert", check_alert)

    def check_full_report():
        import random
        random.seed(42)
        returns = [0.005 + random.uniform(-0.003, 0.003) for _ in range(30)]
        returns += [random.uniform(-0.015, 0.01) for _ in range(20)]
        report = degradation_report(returns)
        assert "health" in report
        assert "alert" in report
        return f"status={report['health']['status']}, alert={report['alert']['severity']}"

    check("degradation_report (full)", check_full_report)

    # ─── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"Results: {results['passed']} passed, {results['failed']} failed")
    total = results['passed'] + results['failed']
    print(f"Total: {total} checks")

    if results["failed"] > 0:
        print("\nFAILURES:")
        for c in results["checks"]:
            if c["status"] == "FAIL":
                print(f"  - {c['name']}: {c['detail']}")

    # Save report
    report_path = CODE_DIR / "quality_guards_dryrun_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nReport saved: {report_path}")

    return results["failed"] == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
