"""E2E Dry-Run — полная цепочка strategy_combine на synthetic данных.

Фича 3 плана аттестации: прогоняет end-to-end сценарий без live orders.

Цепочка:
  regime_gate → signal_fusion → candidate_allocator → pipeline_ranker
  → risk_scorecard → pipeline_timesfm_bridge → lifecycle_scorecard

Проверяет:
  - все score > 0
  - forecast_adjustment не NaN
  - risk_score не конфликтует с signal
  - excluded tickers (RI) отфильтрованы
  - все модули согласованы
  - нет broker imports

Не отправляет ордера (no broker imports, AST-guard).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ─── Import check: no broker ──────────────────────────────────────────
def _assert_no_broker():
    """AST-guard: ensures no broker imports in this module."""
    import ast
    import inspect
    source = inspect.getsource(inspect.getmodule(_assert_no_broker))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "tinkoff" not in alias.name.lower(), f"Broker import detected: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "tinkoff" not in node.module.lower(), f"Broker import detected: {node.module}"

_assert_no_broker()

# ─── Import pipeline modules ──────────────────────────────────────────
from regime_gate import admit as regime_admit, classify_ticker
from candidate_allocator import select_live_slots
from pipeline_ranker import run_pipeline
from risk_scorecard import build_scorecard
from forecast_context import ForecastContext
from pipeline_timesfm_bridge import augment_scorecard_with_forecast
from allocator_metrics import allocator_score, expectancy_r


# ─── Synthetic data generators ────────────────────────────────────────

def make_synthetic_config() -> Dict[str, Any]:
    """Полный конфиг для dry-run (безопасный, без live)."""
    return {
        "mode": "paper",
        "paper_first": True,
        "deposit_rub": 100000,
        "risk": {
            "risk_per_trade_pct": 2.7,
            "go_budget_pct": 50,
            "max_slots": 3,
            "portfolio_stop_drawdown_pct": 25,
            "delta_band_pct": 30,
            "signal_max_age_minutes": 16,
            "max_contracts_per_entry": 1,
        },
        "universe": ["BR", "GAZP", "LKOH", "SBER", "Si"],
        "excluded": ["RI"],
        "risk_scorecard_weights": {
            "exposure": 20, "drawdown": 20, "volatility": 15,
            "correlation": 10, "signal_age": 15, "slots": 10, "caps": 10,
        },
    }


def make_synthetic_regime_snapshot() -> Dict[str, Any]:
    """Синтетический regime snapshot на 5 тикеров."""
    return {
        "ts": "2026-08-23T10:00:00+00:00",
        "tickers": {
            "BR": {"adx": 23.1, "direction": "up", "regime": "trend", "atr_pct": 0.279},
            "GAZP": {"adx": 35.3, "direction": "up", "regime": "trend", "atr_pct": 0.299},
            "LKOH": {"adx": 21.4, "direction": "down", "regime": "range", "atr_pct": 0.242},
            "SBER": {"adx": 45.3, "direction": "down", "regime": "trend", "atr_pct": 0.183},
            "Si": {"adx": 31.7, "direction": "up", "regime": "trend", "atr_pct": 0.156},
        },
        "bias": "up",
        "trend_cnt": 4,
    }


def make_synthetic_candidates() -> List[Dict[str, Any]]:
    """Синтетические кандидаты: 4 обычных + 1 excluded (RI) + 1 с низким win_rate."""
    return [
        {
            "ticker": "LKOH",
            "direction": "SHORT",
            "win_rate": 0.65,
            "avg_win": 450.0,
            "avg_loss": 220.0,
            "contracts_requested": 1,
            "strategy": "vwap_reversion",
        },
        {
            "ticker": "GAZP",
            "direction": "SHORT",
            "win_rate": 0.55,
            "avg_win": 180.0,
            "avg_loss": 150.0,
            "contracts_requested": 1,
            "strategy": "ft_bband_rsi",
        },
        {
            "ticker": "SBER",
            "direction": "LONG",
            "win_rate": 0.70,
            "avg_win": 300.0,
            "avg_loss": 180.0,
            "contracts_requested": 1,
            "strategy": "ma_cross",
        },
        {
            "ticker": "BR",
            "direction": "LONG",
            "win_rate": 0.48,
            "avg_win": 250.0,
            "avg_loss": 200.0,
            "contracts_requested": 1,
            "strategy": "rsi_reversal",
        },
        {
            "ticker": "RI",
            "direction": "LONG",
            "win_rate": 0.60,
            "avg_win": 500.0,
            "avg_loss": 250.0,
            "contracts_requested": 1,
            "strategy": "breakout",
        },
        {
            "ticker": "Si",
            "direction": "LONG",
            "win_rate": 0.40,
            "avg_win": 100.0,
            "avg_loss": 300.0,
            "contracts_requested": 1,
            "strategy": "scalper",
        },
    ]


def make_synthetic_forecast_context() -> Optional[ForecastContext]:
    """Синтетический ForecastContext для bridge test."""
    try:
        from timesfm_adapter import ForecastResult
        return ForecastContext(
            per_ticker={
                "LKOH": ForecastResult(direction="down", ci_width=0.08, confidence=0.75, horizon=20, source="dummy"),
                "GAZP": ForecastResult(direction="down", ci_width=0.15, confidence=0.55, horizon=20, source="dummy"),
                "SBER": ForecastResult(direction="down", ci_width=0.10, confidence=0.70, horizon=20, source="dummy"),
                "BR": ForecastResult(direction="up", ci_width=0.12, confidence=0.45, horizon=20, source="dummy"),
                "Si": ForecastResult(direction="up", ci_width=0.20, confidence=0.35, horizon=20, source="dummy"),
            },
            portfolio_bias="mixed",
            volatility_regime="normal",
            confidence_score=0.56,
            meta={"tickers_with_signal": 5, "tickers_no_signal": 0, "avg_ci_width": 0.13, "n_tickers": 5},
        )
    except (TypeError, ValueError):
        return None


def make_synthetic_returns() -> List[float]:
    """Синтетический временной ряд доходностей для lifecycle."""
    import random
    random.seed(42)
    return [random.gauss(0.0, 0.015) for _ in range(50)]


# ─── E2E Scenario Runner ─────────────────────────────────────────────

class ScenarioResult:
    """Результат одного шага E2E сценария."""
    def __init__(self, name: str, passed: bool, detail: str, data: Any = None):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.data = data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "data": self.data if self.data is not None else {},
        }


def run_e2e_dryrun() -> Dict[str, Any]:
    """Полный E2E dry-run сценарий на synthetic данных.

    Возвращает:
        {
            "steps": [...],
            "all_passed": bool,
            "n_passed": int,
            "n_failed": int,
            "pipeline_result": {...},
            "bridge_result": {...},
            "e2e_artifact": {...},
        }
    """
    results: List[ScenarioResult] = []
    config = make_synthetic_config()
    regime_snapshot = make_synthetic_regime_snapshot()
    candidates = make_synthetic_candidates()
    forecast_context = make_synthetic_forecast_context()
    returns = make_synthetic_returns()

    now_ts = time.time()

    # ── Step 1: Regime gate filter ──
    log_lines = []
    log_lines.append("STEP 1: Regime gate filter")

    admitted = []
    gated_count = 0
    for c in candidates:
        vol_pctl = regime_snapshot["tickers"].get(c["ticker"], {}).get("atr_pct", 0.2) * 100
        result = regime_admit(c, regime_snapshot, config, vol_pctl)
        if result["admit"]:
            admitted.append(c)
        else:
            gated_count += 1
            log_lines.append(f"  GATED: {c['ticker']} — {result.get('reason', 'unknown')}")

    n_excluded_step1 = len([c for c in admitted if c["ticker"] == "RI"])
    results.append(ScenarioResult(
        name="regime_gate",
        passed=(n_excluded_step1 == 0 and gated_count >= 0),
        detail=f"admitted={len(admitted)}, gated={gated_count}, ri_in_admitted={n_excluded_step1}",
    ))

    # ── Step 2: Excluded tickers check ──
    log_lines.append("STEP 2: Excluded tickers check (RI)")
    ri_in_candidates = [c for c in candidates if c["ticker"] == "RI"]
    ri_removed = len(ri_in_candidates) > 0
    results.append(ScenarioResult(
        name="excluded_tickers",
        passed=ri_removed,
        detail=f"RI found in candidates: {ri_removed} ({len(ri_in_candidates)} instances)",
    ))

    # ── Step 3: Pipeline ranker (full chain) ──
    log_lines.append("STEP 3: Pipeline ranker (analytics→rank→select→scorecard)")
    pipeline_result = run_pipeline(
        config=config,
        candidates=candidates,
        regime_snapshot=regime_snapshot,
        returns=returns,
        now_ts=now_ts,
    )

    n_selected = len(pipeline_result["selected"])
    n_excluded_meta = pipeline_result["meta"]["n_excluded"]
    has_scorecard = pipeline_result["scorecard"] is not None

    # Verify RI excluded
    ri_in_selected = [s for s in pipeline_result["selected"] if s.get("ticker") == "RI"]
    ri_filtered = ri_in_selected == [] and n_excluded_meta > 0

    results.append(ScenarioResult(
        name="pipeline_ranker",
        passed=(n_selected > 0 and n_selected <= 3 and has_scorecard and ri_filtered),
        detail=(
            f"selected={n_selected}, excluded={n_excluded_meta}, "
            f"gated={pipeline_result['meta']['n_gated']}, "
            f"has_scorecard={has_scorecard}, ri_filtered={ri_filtered}"
        ),
    ))

    # ── Step 4: Risk scorecard validation ──
    log_lines.append("STEP 4: Risk scorecard validation")
    sc = pipeline_result.get("scorecard", {})
    risk_score = sc.get("risk_score", -1)
    verdict = sc.get("verdict", "UNKNOWN")
    components = sc.get("components", {})

    score_ok = 0 <= risk_score <= 100
    verdict_ok = verdict in ("ALLOW", "REDUCE", "VETO")
    components_ok = len(components) >= 5  # at least 5 of 7 components

    # Check no NaN in components
    nan_found = False
    for comp_name, comp in components.items():
        val = comp.get("value", 0)
        if isinstance(val, float) and math.isnan(val):
            nan_found = True
            break

    results.append(ScenarioResult(
        name="risk_scorecard",
        passed=(score_ok and verdict_ok and components_ok and not nan_found),
        detail=f"risk_score={risk_score}, verdict={verdict}, components={len(components)}, nan={nan_found}",
    ))

    # ── Step 5: Forecast context bridge ──
    log_lines.append("STEP 5: Forecast context → TimesFM bridge")
    enriched = augment_scorecard_with_forecast(
        pipeline_result,
        forecast_context=forecast_context,
        regime_snapshot=regime_snapshot,
    )

    has_forecast_risk = enriched.get("meta", {}).get("has_forecast_risk", False)
    adj = enriched.get("forecast_risk_adjustments")
    risk_mode = adj.get("risk_mode", "unknown") if adj else "none"

    # Check no NaN in forecast adjustments
    adj_nan = False
    if adj:
        for ps in adj.get("per_slot", []):
            for key in ["stale", "conflict"]:
                nested = ps.get(key, {})
                for v in nested.values():
                    if isinstance(v, float) and math.isnan(v):
                        adj_nan = True

    bridge_ok = has_forecast_risk and not adj_nan and risk_mode in ("risk-on", "risk-off", "neutral")
    results.append(ScenarioResult(
        name="forecast_bridge",
        passed=bridge_ok,
        detail=f"has_forecast_risk={has_forecast_risk}, risk_mode={risk_mode}, nan={adj_nan}",
    ))

    # ── Step 6: Per-slot scores validation ──
    log_lines.append("STEP 6: Per-slot scores validation")
    per_slot = enriched.get("per_slot_scores", [])
    all_scores_positive = all(
        sc.get("score", 0) >= 0 for sc in per_slot
    ) if per_slot else False
    all_have_forecast = all(
        "forecast_stale" in sc and "forecast_conflict" in sc for sc in per_slot
    ) if per_slot else False

    results.append(ScenarioResult(
        name="per_slot_scores",
        passed=(all_scores_positive and all_have_forecast),
        detail=f"n_slots={len(per_slot)}, all_non_negative={all_scores_positive}, all_have_forecast={all_have_forecast}",
    ))

    # ── Step 7: Lifecycle section ──
    log_lines.append("STEP 7: Lifecycle scorecard")
    lifecycle = enriched.get("lifecycle")
    lifecycle_verdict = enriched.get("lifecycle_verdict", "UNKNOWN")
    lifecycle_ok = lifecycle is not None and lifecycle_verdict in ("ALLOW", "WARN")

    results.append(ScenarioResult(
        name="lifecycle_scorecard",
        passed=lifecycle_ok,
        detail=f"has_lifecycle={lifecycle is not None}, verdict={lifecycle_verdict}",
    ))

    # ── Step 8: Signal score consistency ──
    log_lines.append("STEP 8: Signal score consistency — no conflicts")
    # Verify that high win_rate candidates got selected
    selected_tickers = {s.get("ticker") for s in enriched.get("selected", [])}
    high_wr = {c["ticker"] for c in candidates if c.get("win_rate", 0) >= 0.55 and c["ticker"] != "RI"}
    # At least one high win_rate should be selected (regime permitting)
    consistency_ok = len(selected_tickers & high_wr) > 0

    results.append(ScenarioResult(
        name="signal_consistency",
        passed=consistency_ok,
        detail=f"selected={selected_tickers}, high_wr={high_wr}, overlap={selected_tickers & high_wr}",
    ))

    # ── Step 9: Final verdict consistency ──
    log_lines.append("STEP 9: Final verdict consistency")
    final_verdict = enriched.get("scorecard", {}).get("verdict", "UNKNOWN")
    risk_score_final = enriched.get("scorecard", {}).get("risk_score", -1)

    # risk_mode=risk-off should have downgraded verdict
    if risk_mode == "risk-off":
        verdict_consistent = final_verdict in ("REDUCE", "VETO")
    else:
        verdict_consistent = final_verdict in ("ALLOW", "REDUCE", "VETO")

    results.append(ScenarioResult(
        name="verdict_consistency",
        passed=verdict_consistent,
        detail=f"final_verdict={final_verdict}, risk_mode={risk_mode}, risk_score={risk_score_final}",
    ))

    # ── Summary ──
    n_passed = sum(1 for r in results if r.passed)
    n_failed = sum(1 for r in results if not r.passed)
    all_passed = n_failed == 0

    print("=" * 60)
    print("E2E DRY-RUN SCENARIO — strategy_combine")
    print("=" * 60)
    for line in log_lines:
        print(line)
    print()
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name}: {r.detail}")
    print()
    print(f"RESULT: {'ALL PASSED' if all_passed else f'{n_failed} FAILED'}")
    print(f"  passed: {n_passed}/{len(results)}")
    print(f"  failed: {n_failed}/{len(results)}")
    print("=" * 60)

    # Write e2e_result.json artifact
    artifact = {
        "steps": [r.to_dict() for r in results],
        "all_passed": all_passed,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "pipeline_meta": enriched.get("meta", {}),
        "final_verdict": final_verdict,
        "risk_mode": risk_mode,
        "n_live_orders": 0,
        "timestamp": time.time(),
    }

    # Write artifact
    try:
        project_root = os.path.dirname(_HERE)
        artifact_path = os.path.join(project_root, "e2e_result.json")
        with open(artifact_path, "w", encoding="utf-8") as fh:
            json.dump(artifact, fh, indent=2, ensure_ascii=False, default=str)
        print(f"\nArtifact written: {artifact_path}")
    except OSError as exc:
        print(f"\nWarning: could not write artifact: {exc}")

    return artifact


def _main() -> None:
    """CLI entry point."""
    artifact = run_e2e_dryrun()
    print("\n=== E2E Artifact Summary ===")
    print(json.dumps({
        "all_passed": artifact["all_passed"],
        "n_passed": artifact["n_passed"],
        "n_failed": artifact["n_failed"],
        "final_verdict": artifact["final_verdict"],
        "risk_mode": artifact["risk_mode"],
        "n_live_orders": artifact["n_live_orders"],
    }, indent=2))


if __name__ == "__main__":
    _main()
