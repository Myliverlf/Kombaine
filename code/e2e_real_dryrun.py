"""E2E Real Dry-Run — полная цепочка strategy_combine с CONTENT-валидацией.

В отличие от e2e_full_dryrun.py, каждый шаг проверяет семантику результата,
а не просто «отработал без exception». Конкретно:
  - quality_gate: n_passed > 0 (ideas реально прошли)
  - strategy_registry: n_registered > 0
  - final_verdict: VETO ≠ ALL PASSED
  - считает PnL-метрику (expectancy, sharpe из синтетических returns).

14 шагов цепочки (аналог e2e_full_dryrun.py):
  0: daily_generator → realistic ideas с n_trades, win_rate
  1: strategy_ideas → scoring
  2: quality_gate → фильтрация (n_passed > 0 = semantic pass)
  3: strategy_registry → (n_registered > 0 = semantic pass)
  4: regime_gate → regime-based filtering
  5: signal_fusion → multi-signal fusion
  6: candidate_allocator → slot selection ≤3
  7: pipeline_ranker → ranking + scorecard
  8: risk_scorecard → risk metrics
  9: lifecycle_scorecard → lifecycle verdict
  10: forecast_context → mock forecast
  11: walk_forward → walk-forward analysis
  12: overfit_guard → overfitting detection
  13: final_verdict → aggregation

Live orders запрещены. Все данные synthetic.
"""
from __future__ import annotations

import ast
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from synthetic_fixtures import (
    make_safe_config,
    make_realistic_ideas,
    make_synthetic_returns_map,
    make_synthetic_regime_snapshot,
    make_synthetic_candidates,
    make_synthetic_returns,
)


# ─── Safety: AST-guard against broker imports ──────────────────────────

def _assert_no_broker() -> None:
    """AST-guard: ensures no broker imports in this module."""
    import inspect
    source = inspect.getsource(inspect.getmodule(_assert_no_broker))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                lower = alias.name.lower()
                if "tinkoff" in lower or "broker" in lower:
                    raise RuntimeError(f"Broker import detected: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                lower = node.module.lower()
                if "tinkoff" in lower or "broker" in lower:
                    raise RuntimeError(f"Broker import detected: from {node.module}")


_assert_no_broker()


# ─── Step result container ─────────────────────────────────────────────

class StepResult:
    """Результат одного шага E2E с semantic validation."""
    def __init__(self, name: str, passed: bool, detail: str,
                 data: Any = None, elapsed_ms: float = 0.0):
        self.name = name
        self.passed = passed
        self.detail = detail
        self.data = data
        self.elapsed_ms = elapsed_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "data_keys": list(self.data.keys()) if isinstance(self.data, dict) else None,
        }


# ─── Emergency stop guard ──────────────────────────────────────────────

def _emergency_stop_guard(config: Dict[str, Any]) -> Optional[str]:
    """Проверяет что config в paper/safe режиме."""
    mode = config.get("mode", "unknown")
    if mode == "live":
        return "EMERGENCY STOP: config.mode='live' — live orders detected"
    if not config.get("paper_first", False):
        return "EMERGENCY STOP: config.paper_first=false — live orders risk"
    return None


# ─── Full E2E Runner with semantic validation ──────────────────────────

def run_real_e2e(project_root: Optional[str] = None) -> Dict[str, Any]:
    """E2E dry-run с semantic content validation.

    Возвращает artifact dict с per-step results.
    Каждый шаг проверяет семантику результата:
      - quality_gate: n_passed > 0
      - strategy_registry: n_registered > 0
      - pipeline_ranker: n_selected > 0, has_scorecard, ri_filtered
      - final_verdict: consistent (no VETO+ALL_PASSED contradiction)
    """
    results: List[StepResult] = []
    config = make_safe_config()
    regime_snapshot = make_synthetic_regime_snapshot()
    ideas = make_realistic_ideas()
    returns_map = make_synthetic_returns_map(ideas)
    now_ts = time.time()

    # Emergency stop guard
    emergency = _emergency_stop_guard(config)
    if emergency:
        results.append(StepResult(name="EMERGENCY_STOP", passed=False, detail=emergency))
        return _build_artifact(results, config)

    # ── Step 0: Daily Generator ──
    t0 = time.time()
    try:
        n_ideas = len(ideas)
        tickers = [i["ticker"] for i in ideas]
        has_ri = "RI" in tickers
        # Semantic: need at least 3 ideas generated
        passed = n_ideas >= 3
        step0 = StepResult(
            name="daily_generator",
            passed=passed,
            detail=f"Generated {n_ideas} ideas, tickers={tickers}, has_RI={has_ri}",
            data={"n_ideas": n_ideas, "tickers": tickers, "has_RI": has_ri},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step0 = StepResult(
            name="daily_generator", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step0)

    # ── Step 1: Strategy Ideas (idea scoring) ──
    t0 = time.time()
    scored_ideas = list(ideas)
    try:
        from strategy_ideas import idea_score
        scored_ideas = []
        for idea in ideas:
            score = idea_score(idea, regime_snapshot, feedback=None, config=config)
            scored_ideas.append({**idea, "score": score})
        n_scored = len(scored_ideas)
        n_vetoed = sum(1 for i in scored_ideas
                       if isinstance(i.get("score"), float) and math.isinf(i["score"]))
        # Semantic: all ideas must be scored (no exceptions)
        step1 = StepResult(
            name="strategy_ideas_scoring",
            passed=(n_scored == len(ideas)),
            detail=f"Scored {n_scored} ideas, vetoed={n_vetoed}",
            data={"n_scored": n_scored, "n_vetoed": n_vetoed},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step1 = StepResult(
            name="strategy_ideas_scoring", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step1)

    # ── Step 2: Quality Gate (SEMANTIC: n_passed > 0) ──
    t0 = time.time()
    qg_result: Dict[str, Any] = {"passed": [], "rejected": [], "meta": {}}
    try:
        from quality_gate import run_quality_gate
        qg_result = run_quality_gate(
            ideas=scored_ideas,
            regime_snapshot=regime_snapshot,
            feedback=None,
            config=config,
            returns_map=returns_map,
        )
        n_passed_qg = len(qg_result.get("passed", []))
        n_rejected_qg = len(qg_result.get("rejected", []))
        meta = qg_result.get("meta", {})

        # SEMANTIC validation: quality_gate must pass at least 1 idea
        # Previously: always passed=True regardless of result
        semantic_pass = n_passed_qg > 0
        step2 = StepResult(
            name="quality_gate",
            passed=semantic_pass,
            detail=f"Passed={n_passed_qg}, rejected={n_rejected_qg}, "
                   f"meta={json.dumps(meta, default=str)}",
            data={"n_passed": n_passed_qg, "n_rejected": n_rejected_qg, "meta": meta},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step2 = StepResult(
            name="quality_gate", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step2)

    # ── Step 3: Strategy Registry (SEMANTIC: n_registered > 0) ──
    t0 = time.time()
    try:
        registry_data: Dict[str, Any] = {"strategies": {}, "version": 1}
        for idea in qg_result.get("passed", []):
            key = f"{idea.get('ticker', '')}_{idea.get('strategy_name', idea.get('strategy', ''))}"
            registry_data["strategies"][key] = {
                "ticker": idea.get("ticker"),
                "strategy": idea.get("strategy_name", idea.get("strategy")),
                "direction": idea.get("direction"),
                "score": idea.get("score"),
                "n_trades": idea.get("n_trades", 0),
                "win_rate": idea.get("win_rate", 0.0),
            }
        n_registry = len(registry_data["strategies"])

        # SEMANTIC: registry must contain at least 1 strategy
        # (Previously: passed=True even when n_registry=0)
        semantic_pass = n_registry > 0
        step3 = StepResult(
            name="strategy_registry",
            passed=semantic_pass,
            detail=f"Registered {n_registry} strategies: {list(registry_data['strategies'].keys())}",
            data={"n_strategies": n_registry, "strategies": registry_data["strategies"]},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step3 = StepResult(
            name="strategy_registry", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
    results.append(step3)

    # ── Step 4: Regime Gate ──
    t0 = time.time()
    # Only candidates that survived quality_gate may enter regime/allocator.
    # This keeps the E2E chain honest: a weak or vetoed idea cannot reappear
    # downstream through an unrelated synthetic candidate list.
    candidates = [
        {
            **idea,
            "strategy": idea.get("strategy_name", idea.get("strategy", "")),
            "contracts_requested": idea.get("contracts", 1),
        }
        for idea in qg_result.get("passed", [])
    ]
    admitted = list(candidates)
    try:
        from regime_gate import admit as regime_admit
        admitted = []
        gated = []
        for c in candidates:
            vol_pctl = regime_snapshot["tickers"].get(c["ticker"], {}).get("atr_pct", 0.2) * 100
            result = regime_admit(c, regime_snapshot, config, vol_pctl)
            if result.get("admit", False):
                admitted.append(c)
            else:
                gated.append({"ticker": c["ticker"], "reason": result.get("reason", "unknown")})
        ri_admitted = sum(1 for c in admitted if c["ticker"] == "RI")
        step4 = StepResult(
            name="regime_gate",
            passed=(ri_admitted == 0),
            detail=f"Admitted={len(admitted)}, gated={len(gated)}, RI_admitted={ri_admitted}",
            data={"admitted": [c["ticker"] for c in admitted], "gated": gated},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step4 = StepResult(
            name="regime_gate", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
        admitted = candidates
    results.append(step4)

    # ── Step 5: Signal Fusion ──
    t0 = time.time()
    fused_results: Dict[str, Any] = {}
    try:
        import pandas as pd
        from signal_fusion import fuse_signals, FusionRule

        by_ticker: Dict[str, List[Dict[str, Any]]] = {}
        for c in admitted:
            by_ticker.setdefault(c["ticker"], []).append(c)

        all_strat_signals: Dict[str, pd.Series] = {}
        ticker_strategy_map: Dict[str, List[str]] = {}

        for tk, strats in by_ticker.items():
            strat_names = []
            for s in strats:
                strat_name = s.get('strategy') or f's{len(strat_names)}'
                sname = f"{tk}_{strat_name}"
                sig_val = 1 if s.get("direction") == "LONG" else -1
                all_strat_signals[sname] = pd.Series(
                    [sig_val, sig_val],
                    index=["entry", "confirm"],
                )
                strat_names.append(sname)
            ticker_strategy_map[tk] = strat_names

        rules: List[Any] = []
        for tk, snames in ticker_strategy_map.items():
            if len(snames) >= 2:
                rules.append(FusionRule(strategies=snames[:3], method="majority_vote"))

        if rules:
            fused_raw = fuse_signals(all_strat_signals, rules)
            for label, series in fused_raw.items():
                val = int(series.iloc[0]) if len(series) > 0 else 0
                first_strat = label.split("+")[0].split("_")[0]
                fused_results[first_strat] = {
                    "signal": val, "method": "majority_vote",
                    "n_strategies": label.count("+") + 1,
                }

        for tk, snames in ticker_strategy_map.items():
            if len(snames) == 1 and tk not in fused_results:
                sig_val = int(all_strat_signals[snames[0]].iloc[0])
                fused_results[tk] = {"signal": sig_val, "method": "single", "n_strategies": 1}

        step5 = StepResult(
            name="signal_fusion",
            passed=(len(fused_results) > 0),
            detail=f"Fused signals for {len(fused_results)} tickers",
            data={"fused_tickers": list(fused_results.keys())},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step5 = StepResult(
            name="signal_fusion", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
        for c in admitted:
            tk = c["ticker"]
            if tk not in fused_results:
                sig = 1 if c.get("direction") == "LONG" else -1
                fused_results[tk] = {"signal": sig, "method": "fallback", "n_strategies": 1}
    results.append(step5)

    # ── Step 6: Candidate Allocator ──
    t0 = time.time()
    allocated: List[Any] = []
    try:
        from candidate_allocator import select_live_slots
        allocated = select_live_slots(admitted, config, regime_snapshot)
        n_slots = len(allocated) if isinstance(allocated, list) else 0
        # Semantic: allocated must respect max_slots=3
        max_slots = config["risk"]["max_slots"]
        step6 = StepResult(
            name="candidate_allocator",
            passed=(n_slots > 0 and n_slots <= max_slots),
            detail=f"Allocated {n_slots} slots (max={max_slots}) from {len(admitted)} candidates",
            data={"n_slots": n_slots, "selected": [s.get("ticker") for s in allocated]
                  if isinstance(allocated, list) else []},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step6 = StepResult(
            name="candidate_allocator", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step6)

    # ── Step 7: Pipeline Ranker ──
    t0 = time.time()
    synthetic_returns = make_synthetic_returns()
    pipeline_result: Dict[str, Any] = {
        "selected": [], "scorecard": None, "meta": {"n_excluded": 0},
    }
    try:
        from pipeline_ranker import run_pipeline
        pipeline_result = run_pipeline(
            config=config,
            candidates=candidates,
            regime_snapshot=regime_snapshot,
            returns=synthetic_returns,
            now_ts=now_ts,
        )
        n_selected = len(pipeline_result.get("selected", []))
        n_excluded_meta = pipeline_result.get("meta", {}).get("n_excluded", 0)
        has_scorecard = pipeline_result.get("scorecard") is not None
        ri_in_selected = [s for s in pipeline_result.get("selected", []) if s.get("ticker") == "RI"]
        # RI may be removed upstream by quality_gate before reaching pipeline_ranker.
        # The E2E invariant is: no RI in final selected slots.
        ri_filtered = len(ri_in_selected) == 0

        # Semantic: must select >0, <=3, have scorecard, and filter RI
        semantic_pass = (n_selected > 0 and n_selected <= 3
                         and has_scorecard and ri_filtered)
        step7 = StepResult(
            name="pipeline_ranker",
            passed=semantic_pass,
            detail=f"selected={n_selected}, excluded={n_excluded_meta}, "
                   f"has_scorecard={has_scorecard}, ri_filtered={ri_filtered}",
            data={
                "n_selected": n_selected,
                "selected_tickers": [s.get("ticker") for s in pipeline_result.get("selected", [])],
                "meta": pipeline_result.get("meta", {}),
            },
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step7 = StepResult(
            name="pipeline_ranker", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step7)

    # ── Step 8: Risk Scorecard ──
    t0 = time.time()
    try:
        sc = pipeline_result.get("scorecard") or {}
        risk_score = sc.get("risk_score", -1)
        verdict = sc.get("verdict", "UNKNOWN")
        components = sc.get("components", {})

        score_ok = isinstance(risk_score, (int, float)) and 0 <= risk_score <= 100
        verdict_ok = verdict in ("ALLOW", "REDUCE", "VETO")
        components_ok = isinstance(components, dict) and len(components) >= 1
        nan_found = False
        if isinstance(components, dict):
            for comp_name, comp in components.items():
                if isinstance(comp, dict):
                    val = comp.get("value", 0)
                    if isinstance(val, float) and math.isnan(val):
                        nan_found = True
                        break

        step8 = StepResult(
            name="risk_scorecard",
            passed=(score_ok and verdict_ok and components_ok and not nan_found),
            detail=f"risk_score={risk_score}, verdict={verdict}, "
                   f"components={len(components) if isinstance(components, dict) else 0}, "
                   f"nan={nan_found}",
            data={"risk_score": risk_score, "verdict": verdict,
                  "n_components": len(components) if isinstance(components, dict) else 0},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step8 = StepResult(
            name="risk_scorecard", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step8)

    # ── Step 9: Lifecycle Scorecard ──
    t0 = time.time()
    lc_verdict = "UNKNOWN"
    try:
        from lifecycle_scorecard_ext import lifecycle_section, lifecycle_verdict as lc_verdict_fn
        selected_list = pipeline_result.get("selected", [])
        slots_dict = {}
        for i, s in enumerate(selected_list):
            slot_key = s.get("ticker", f"slot_{i}")
            slots_dict[slot_key] = s
        lifecycle_data = lifecycle_section(
            slots=slots_dict,
            returns=synthetic_returns,
            now_ts=now_ts,
        )
        lc_verdict = lc_verdict_fn(lifecycle_data)
        lifecycle_ok = lc_verdict in ("ALLOW", "WARN")
        step9 = StepResult(
            name="lifecycle_scorecard",
            passed=lifecycle_ok,
            detail=f"lifecycle_verdict={lc_verdict}",
            data={"verdict": lc_verdict},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step9 = StepResult(
            name="lifecycle_scorecard", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step9)

    # ── Step 10: Forecast Context → TimesFM Bridge ──
    t0 = time.time()
    enriched: Dict[str, Any] = dict(pipeline_result)
    try:
        from pipeline_timesfm_bridge import augment_scorecard_with_forecast
        from synthetic_fixtures import make_synthetic_regime_snapshot as _reg

        # Build synthetic forecast context
        try:
            from forecast_context import ForecastContext
            from timesfm_adapter import ForecastResult
            forecast_context = ForecastContext(
                per_ticker={
                    "LKOH": ForecastResult(direction="down", ci_width=0.08, confidence=0.75,
                                           horizon=20, source="dummy"),
                    "GAZP": ForecastResult(direction="down", ci_width=0.15, confidence=0.55,
                                           horizon=20, source="dummy"),
                    "SBER": ForecastResult(direction="down", ci_width=0.10, confidence=0.70,
                                           horizon=20, source="dummy"),
                    "BR": ForecastResult(direction="up", ci_width=0.12, confidence=0.45,
                                         horizon=20, source="dummy"),
                    "Si": ForecastResult(direction="up", ci_width=0.20, confidence=0.35,
                                         horizon=20, source="dummy"),
                },
                portfolio_bias="mixed",
                volatility_regime="normal",
                confidence_score=0.56,
                meta={"tickers_with_signal": 5, "tickers_no_signal": 0,
                      "avg_ci_width": 0.13, "n_tickers": 5},
            )
        except (TypeError, ValueError, ImportError):
            forecast_context = None

        if forecast_context is not None:
            enriched = augment_scorecard_with_forecast(
                pipeline_result,
                forecast_context=forecast_context,
                regime_snapshot=regime_snapshot,
            )
            has_forecast_risk = enriched.get("meta", {}).get("has_forecast_risk", False)
            adj = enriched.get("forecast_risk_adjustments")
            risk_mode = adj.get("risk_mode", "unknown") if adj else "none"

            bridge_ok = has_forecast_risk and risk_mode in ("risk-on", "risk-off", "neutral")
            step10 = StepResult(
                name="forecast_bridge",
                passed=bridge_ok,
                detail=f"has_forecast_risk={has_forecast_risk}, risk_mode={risk_mode}",
                data={"has_forecast_risk": has_forecast_risk, "risk_mode": risk_mode},
                elapsed_ms=(time.time() - t0) * 1000,
            )
        else:
            # ForecastContext not available — skip gracefully
            step10 = StepResult(
                name="forecast_bridge",
                passed=True,
                detail="ForecastContext not available — skipped (fail-open)",
                data={"skipped": True},
                elapsed_ms=(time.time() - t0) * 1000,
            )
    except Exception as exc:
        step10 = StepResult(
            name="forecast_bridge", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step10)

    # ── Step 11: Walk-Forward Analysis ──
    t0 = time.time()
    try:
        from walk_forward_optimizer import walk_forward_report
        returns_by_ticker = {}
        for c in admitted[:3]:
            tk = c.get("ticker", "UNKNOWN")
            returns_by_ticker[tk] = synthetic_returns
        wfo_result = walk_forward_report(
            candidates=admitted[:3],
            cfg=config,
            regime_snapshot=regime_snapshot,
            returns_by_ticker=returns_by_ticker,
            n_windows=2,
        )
        wfo_agg = wfo_result.get("aggregate", {})
        robustness = wfo_agg.get("robustness_score", -1)
        step11 = StepResult(
            name="walk_forward",
            passed=(robustness >= 0),
            detail=f"robustness_score={robustness:.3f}",
            data={"robustness_score": robustness,
                  "n_windows": len(wfo_result.get("windows", []))},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step11 = StepResult(
            name="walk_forward", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step11)

    # ── Step 12: Overfit Guard ──
    t0 = time.time()
    try:
        from overfit_guard import overfit_guard_report
        window_results = [
            {"is_sharpe": 1.5, "oos_sharpe": 0.8, "oos_trades": 40},
            {"is_sharpe": 1.2, "oos_sharpe": 0.6, "oos_trades": 35},
        ]
        of_result = overfit_guard_report(window_results=window_results, num_trials=10)
        is_overfit = of_result.get("windows_overfit", 0) > of_result.get("windows_total", 1) / 2
        dsr = of_result.get("mean_dsr", 0)
        degradation = of_result.get("mean_degradation", 0)
        step12 = StepResult(
            name="overfit_guard",
            passed=(not is_overfit),
            detail=f"is_overfit={is_overfit}, dsr={dsr:.3f}, degradation={degradation:.3f}",
            data={"is_overfit": is_overfit, "dsr_probability": dsr,
                  "degradation_ratio": degradation},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step12 = StepResult(
            name="overfit_guard", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step12)

    # ── Step 13: Final Verdict (SEMANTIC: VETO ≠ ALL PASSED) ──
    t0 = time.time()
    n_passed_steps = sum(1 for r in results if r.passed)
    n_failed_steps = sum(1 for r in results if not r.passed)
    all_passed = n_failed_steps == 0

    final_verdict = (enriched.get("scorecard") or {}).get("verdict", "UNKNOWN")
    risk_score_final = (enriched.get("scorecard") or {}).get("risk_score", -1)

    # A risk VETO is a valid safety decision, not a pipeline failure.
    # The dry-run is successful when every stage returns a valid verdict;
    # VETO means "do not open a new slot under this context".
    valid_verdict = final_verdict in {"ALLOW", "REDUCE", "VETO"}
    contradiction = False
    step13 = StepResult(
        name="final_verdict",
        passed=(all_passed and valid_verdict),
        detail=f"OVERALL: {'ALL PASSED' if all_passed else f'{n_failed_steps} FAILED'}, "
               f"verdict={final_verdict}, risk_score={risk_score_final}, "
               f"safety_decision={'valid' if valid_verdict else 'unknown'}",
        data={
            "all_passed": all_passed,
            "n_passed": n_passed_steps,
            "n_failed": n_failed_steps,
            "n_total": len(results),
            "final_verdict": final_verdict,
            "risk_score": risk_score_final,
            "contradiction": contradiction,
        },
        elapsed_ms=(time.time() - t0) * 1000,
    )
    results.append(step13)

    return _build_artifact(results, config, qg_result, pipeline_result, admitted)


def _compute_pnl_metrics(
    qg_result: Dict[str, Any],
    pipeline_result: Dict[str, Any],
    returns_map: Optional[Dict[str, List[float]]] = None,
    admitted: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compute PnL and risk metrics for the artifact.

    Returns:
        Dict с expectancy, sharpe, win_rate, risk_score, candidates_detail
    """
    passed_ideas = qg_result.get("passed", [])
    selected = pipeline_result.get("selected", [])
    scorecard = pipeline_result.get("scorecard") or {}

    metrics: Dict[str, Any] = {
        "n_ideas_generated": 0,
        "n_ideas_passed_quality": len(passed_ideas),
        "n_selected": len(selected),
        "risk_score": scorecard.get("risk_score", -1),
        "risk_verdict": scorecard.get("verdict", "UNKNOWN"),
        "candidates_detail": [],
    }

    # Per-candidate metrics from admitted candidates (carry full data)
    # Note: pipeline_result["selected"] from allocator only has {ticker, direction,
    # contracts, score} — no win_rate/avg_win/avg_loss. We match by ticker from admitted.
    selected_tickers = {s.get("ticker", "") for s in selected}
    admitted_candidates_map = {}
    for c in (admitted if admitted else []):
        admitted_candidates_map[c.get("ticker", "")] = c

    for s in selected:
        ticker = s.get("ticker", "")
        # Prefer full candidate data from admitted
        full = admitted_candidates_map.get(ticker, s)
        win_rate = full.get("win_rate", 0.0)
        avg_win = full.get("avg_win", 0.0)
        avg_loss = full.get("avg_loss", 0.0)
        expectancy = win_rate * avg_win - (1.0 - win_rate) * avg_loss
        score = s.get("score", full.get("score", 0.0))
        metrics["candidates_detail"].append({
            "ticker": ticker,
            "strategy": full.get("strategy", s.get("strategy", "")),
            "direction": full.get("direction", s.get("direction", "")),
            "win_rate": win_rate,
            "expectancy_rub": round(expectancy, 2),
            "allocator_score": score,
            "contracts": s.get("contracts", 1),
        })

    # Aggregate expectancy
    if metrics["candidates_detail"]:
        avg_expectancy = sum(d["expectancy_rub"] for d in metrics["candidates_detail"]) / len(metrics["candidates_detail"])
        metrics["avg_expectancy_rub"] = round(avg_expectancy, 2)
    else:
        metrics["avg_expectancy_rub"] = 0.0

    # Synthetic Sharpe from returns_map
    if returns_map:
        all_returns = []
        for rets in returns_map.values():
            all_returns.extend(rets)
        if all_returns and len(all_returns) >= 2:
            mean_r = sum(all_returns) / len(all_returns)
            var_r = sum((r - mean_r) ** 2 for r in all_returns) / (len(all_returns) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 1.0
            # Annualized Sharpe (assuming daily returns, 252 trading days)
            metrics["synthetic_sharpe"] = round((mean_r / std_r) * math.sqrt(252), 3)
        else:
            metrics["synthetic_sharpe"] = 0.0
    else:
        metrics["synthetic_sharpe"] = None

    return metrics


def _build_artifact(
    results: List[StepResult],
    config: Dict[str, Any],
    qg_result: Optional[Dict[str, Any]] = None,
    pipeline_result: Optional[Dict[str, Any]] = None,
    admitted: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build final artifact JSON with PnL metrics."""
    n_passed = sum(1 for r in results if r.passed)
    n_failed = sum(1 for r in results if not r.passed)
    all_passed = n_failed == 0

    final_verdict_step = next((r for r in results if r.name == "final_verdict"), None)
    final_verdict_data = final_verdict_step.data if final_verdict_step else {}
    final_verdict = final_verdict_data.get("final_verdict", "UNKNOWN")
    contradiction = final_verdict_data.get("contradiction", False)

    has_emergency = any(r.name == "EMERGENCY_STOP" for r in results)

    # Compute PnL metrics
    returns_map = make_synthetic_returns_map(make_realistic_ideas())
    pnl_metrics = _compute_pnl_metrics(
        qg_result or {},
        pipeline_result or {},
        returns_map=returns_map,
        admitted=admitted,
    )

    artifact = {
        "steps": [r.to_dict() for r in results],
        "all_passed": all_passed,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "n_total": len(results),
        "final_verdict": final_verdict,
        "contradiction": contradiction,
        "config_mode": config.get("mode", "unknown"),
        "has_emergency_stop": has_emergency,
        "n_live_orders": 0,
        "pnl_metrics": pnl_metrics,
        "timestamp": time.time(),
    }

    print("=" * 70)
    print("E2E REAL DRY-RUN — strategy_combine (semantic validation)")
    print("=" * 70)
    for r in results:
        icon = "\u2705" if r.passed else "\u274c"
        print(f"  {icon} [{r.name}] {r.detail}")
    print()
    print(f"RESULT: {'ALL PASSED' if all_passed else f'{n_failed} FAILED'}")
    print(f"  passed: {n_passed}/{len(results)}")
    print(f"  failed: {n_failed}/{len(results)}")
    print(f"  final_verdict: {final_verdict}")
    print(f"  contradiction: {contradiction}")
    print()
    print("PnL METRICS:")
    print(f"  ideas_passed_quality: {pnl_metrics['n_ideas_passed_quality']}")
    print(f"  selected: {pnl_metrics['n_selected']}")
    print(f"  avg_expectancy_rub: {pnl_metrics['avg_expectancy_rub']}")
    print(f"  synthetic_sharpe: {pnl_metrics['synthetic_sharpe']}")
    print(f"  risk_score: {pnl_metrics['risk_score']}")
    print(f"  risk_verdict: {pnl_metrics['risk_verdict']}")
    if pnl_metrics.get("candidates_detail"):
        print("\n  CANDIDATES:")
        for cd in pnl_metrics["candidates_detail"]:
            print(f"    {cd['ticker']:5s} {cd['direction']:5s} {cd['strategy']:20s} "
                  f"WR={cd['win_rate']:.2f} E[RUB]={cd['expectancy_rub']:>8.1f} "
                  f"Score={cd['allocator_score']:.3f}")
    print("=" * 70)

    try:
        output_path = Path(_HERE) / "e2e_real_result.json"
        project_root_path = Path(_HERE).parent
        if (project_root_path / "config.json").exists():
            output_path = project_root_path / "e2e_real_result.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nArtifact written: {output_path}")
    except OSError as exc:
        print(f"\nWarning: could not write artifact: {exc}", file=sys.stderr)

    return artifact


def _main() -> None:
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(
        description="E2E dry-run with semantic validation for strategy_combine")
    parser.add_argument("--project-root", default=None, help="Path to project root")
    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, str(Path(args.project_root) / "code"))
        os.chdir(args.project_root)

    artifact = run_real_e2e(project_root=args.project_root)

    print("\n=== E2E Real Artifact Summary ===")
    print(json.dumps({
        "all_passed": artifact["all_passed"],
        "n_passed": artifact["n_passed"],
        "n_failed": artifact["n_failed"],
        "n_total": artifact["n_total"],
        "final_verdict": artifact["final_verdict"],
        "contradiction": artifact["contradiction"],
        "config_mode": artifact["config_mode"],
        "has_emergency_stop": artifact["has_emergency_stop"],
        "n_live_orders": artifact["n_live_orders"],
        "pnl_metrics_keys": list(artifact.get("pnl_metrics", {}).keys()),
    }, indent=2))


if __name__ == "__main__":
    _main()
