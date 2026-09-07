#!/usr/bin/env python3
"""Full-Chain E2E Dry-Run — полная цепочка strategy_combine на synthetic данных.

Цепочка (14 шагов):
  0: daily_generator — генерирует synthetic ideas
  1: strategy_ideas — парсит ideas → structured signals (scoring)
  2: quality_gate — фильтрует по quality score
  3: strategy_registry — записывает candidates
  4: regime_gate — определяет regime, фильтрует кандидатов
  5: signal_fusion — смешивает multiple signals per ticker
  6: candidate_allocator — распределяет по слотам
  7: pipeline_ranker — ранжирует и строит scorecard
  8: risk_scorecard — считает risk metrics
  9: lifecycle_scorecard — lifecycle оценка
  10: forecast_context → timesfm_bridge — mock forecast
  11: walk_forward — walk-forward analysis (if returns available)
  12: overfit_guard — overfitting detection
  13: final verdict — aggregation + pass/fail

Mock для TimesFM — synthetic forecast без API вызова.
Guard — если на любом шаге detected mode="live" → emergency stop.
Live orders запрещены.

Выход: e2e_full_result.json с per-step status, timing, data shapes.
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
    """Результат одного шага E2E."""
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


# ─── Synthetic data generators ─────────────────────────────────────────

def make_synthetic_config() -> Dict[str, Any]:
    """Безопасный конфиг для dry-run."""
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
            "slot_eject_pf": 0.9,
            "slot_eject_window_trades": 20,
            "slot_eject_streak_stops": 3,
            "slot_eject_slot_drawdown_pct": 15,
            "slot_eject_silent_days": 5,
            "promotion_margin_pct": 10,
            "waitlist_ttl_days": 7,
            "waitlist_max": 20,
            "signal_pool_max": 10,
            "signal_rotation_days": 3,
            "signal_min_rank": 100,
            "min_reserve_pct": 30,
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


def make_synthetic_generator_ideas() -> List[Dict[str, Any]]:
    """Синтетические идеи от daily_generator — имитация шага 0."""
    return [
        {
            "ticker": "LKOH",
            "strategy_name": "vwap_reversion",
            "direction": "SHORT",
            "contracts": 1,
            "params": {"lookback": 30, "entry_z": 0.8, "exit_z": 0.2},
            "source": "daily_generator",
            "generated_at": time.time(),
        },
        {
            "ticker": "GAZP",
            "strategy_name": "ft_bband_rsi",
            "direction": "SHORT",
            "contracts": 1,
            "params": {"bb_period": 20, "rsi_period": 14},
            "source": "daily_generator",
            "generated_at": time.time(),
        },
        {
            "ticker": "SBER",
            "strategy_name": "ma_cross",
            "direction": "LONG",
            "contracts": 1,
            "params": {"fast": 10, "slow": 30},
            "source": "daily_generator",
            "generated_at": time.time(),
        },
        {
            "ticker": "BR",
            "strategy_name": "rsi_reversal",
            "direction": "LONG",
            "contracts": 1,
            "params": {"rsi_period": 14, "threshold": 30},
            "source": "daily_generator",
            "generated_at": time.time(),
        },
        {
            "ticker": "RI",
            "strategy_name": "breakout",
            "direction": "LONG",
            "contracts": 1,
            "params": {"lookback": 20},
            "source": "daily_generator",
            "generated_at": time.time(),
        },
        {
            "ticker": "Si",
            "strategy_name": "scalper",
            "direction": "LONG",
            "contracts": 1,
            "params": {"tick_size": 1},
            "source": "daily_generator",
            "generated_at": time.time(),
        },
    ]


def make_synthetic_candidates() -> List[Dict[str, Any]]:
    """Синтетические кандидаты (после idea scoring) — для allocator."""
    return [
        {
            "ticker": "LKOH", "direction": "SHORT", "win_rate": 0.65,
            "avg_win": 450.0, "avg_loss": 220.0, "contracts_requested": 1,
            "strategy": "vwap_reversion", "score": 0.72,
        },
        {
            "ticker": "GAZP", "direction": "SHORT", "win_rate": 0.55,
            "avg_win": 180.0, "avg_loss": 150.0, "contracts_requested": 1,
            "strategy": "ft_bband_rsi", "score": 0.58,
        },
        {
            "ticker": "SBER", "direction": "LONG", "win_rate": 0.70,
            "avg_win": 300.0, "avg_loss": 180.0, "contracts_requested": 1,
            "strategy": "ma_cross", "score": 0.75,
        },
        {
            "ticker": "BR", "direction": "LONG", "win_rate": 0.48,
            "avg_win": 250.0, "avg_loss": 200.0, "contracts_requested": 1,
            "strategy": "rsi_reversal", "score": 0.45,
        },
        {
            "ticker": "RI", "direction": "LONG", "win_rate": 0.60,
            "avg_win": 500.0, "avg_loss": 250.0, "contracts_requested": 1,
            "strategy": "breakout", "score": 0.62,
        },
        {
            "ticker": "Si", "direction": "LONG", "win_rate": 0.40,
            "avg_win": 100.0, "avg_loss": 300.0, "contracts_requested": 1,
            "strategy": "scalper", "score": 0.35,
        },
    ]


def make_synthetic_forecast_context() -> Optional[Any]:
    """Синтетический ForecastContext для bridge test."""
    try:
        from forecast_context import ForecastContext
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
    except (TypeError, ValueError, ImportError) as exc:
        print(f"  [WARN] Could not create ForecastContext: {exc}")
        return None


def make_synthetic_returns() -> List[float]:
    """Синтетический временной ряд доходностей для lifecycle."""
    import random
    random.seed(42)
    return [random.gauss(0.0, 0.015) for _ in range(50)]


# ─── Emergency stop guard ──────────────────────────────────────────────

def _emergency_stop_guard(config: Dict[str, Any]) -> Optional[str]:
    """Проверяет что config в paper/safe режиме. Returns error string or None."""
    mode = config.get("mode", "unknown")
    if mode == "live":
        return "EMERGENCY STOP: config.mode='live' — live orders detected"
    if not config.get("paper_first", False):
        return "EMERGENCY STOP: config.paper_first=false — live orders risk"
    return None


# ─── Full E2E Runner ───────────────────────────────────────────────────

def run_full_e2e(project_root: Optional[str] = None) -> Dict[str, Any]:
    """Полный E2E dry-run сценарий на synthetic данных."""
    results: List[StepResult] = []
    config = make_synthetic_config()
    regime_snapshot = make_synthetic_regime_snapshot()
    now_ts = time.time()

    # Emergency stop guard
    emergency = _emergency_stop_guard(config)
    if emergency:
        results.append(StepResult(name="EMERGENCY_STOP", passed=False, detail=emergency))
        return _build_artifact(results, config)

    # ── Step 0: Daily Generator ──
    t0 = time.time()
    ideas = []
    try:
        ideas = make_synthetic_generator_ideas()
        n_ideas = len(ideas)
        tickers = [i["ticker"] for i in ideas]
        has_ri = "RI" in tickers
        step0 = StepResult(
            name="daily_generator",
            passed=(n_ideas >= 3),
            detail=f"Generated {n_ideas} ideas, tickers={tickers}, has_RI={has_ri}",
            data={"n_ideas": n_ideas, "tickers": tickers},
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
    scored_ideas = list(ideas)  # fallback
    try:
        from strategy_ideas import idea_score, filter_weak
        scored_ideas = []
        for idea in ideas:
            score = idea_score(idea, regime_snapshot, feedback=None, config=config)
            scored_ideas.append({**idea, "score": score})
        n_scored = len(scored_ideas)
        n_vetoed = sum(1 for i in scored_ideas if isinstance(i.get("score"), float) and math.isinf(i["score"]))
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

    # ── Step 2: Quality Gate ──
    t0 = time.time()
    qg_result = {"passed": scored_ideas, "rejected": []}
    try:
        from quality_gate import run_quality_gate
        qg_result = run_quality_gate(
            ideas=scored_ideas,
            regime_snapshot=regime_snapshot,
            feedback=None,
            config=config,
            returns_map=None,
        )
        n_passed_qg = len(qg_result.get("passed", []))
        n_rejected_qg = len(qg_result.get("rejected", []))
        step2 = StepResult(
            name="quality_gate",
            passed=True,
            detail=f"Passed={n_passed_qg}, rejected={n_rejected_qg}",
            data={"n_passed": n_passed_qg, "n_rejected": n_rejected_qg},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step2 = StepResult(
            name="quality_gate", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step2)

    # ── Step 3: Strategy Registry ──
    t0 = time.time()
    try:
        registry_data = {"strategies": {}, "version": 1}
        for idea in qg_result.get("passed", []):
            key = f"{idea.get('ticker', '')}_{idea.get('strategy_name', idea.get('strategy', ''))}"
            registry_data["strategies"][key] = {
                "ticker": idea.get("ticker"),
                "strategy": idea.get("strategy_name", idea.get("strategy")),
                "direction": idea.get("direction"),
                "score": idea.get("score"),
            }
        n_registry = len(registry_data["strategies"])
        step3 = StepResult(
            name="strategy_registry",
            passed=True,
            detail=f"Registered {n_registry} strategies",
            data={"n_strategies": n_registry},
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
    candidates = make_synthetic_candidates()
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
    # fuse_signals expects {strategy_name: Series} where strategy names
    # are referenced by FusionRule.strategies. We create synthetic multi-
    # strategy signals per ticker from the admitted candidates.
    t0 = time.time()
    fused_results: Dict[str, Any] = {}
    try:
        import pandas as pd
        from signal_fusion import fuse_signals, FusionRule

        # Group admitted candidates by ticker
        by_ticker: Dict[str, List[Dict[str, Any]]] = {}
        for c in admitted:
            by_ticker.setdefault(c["ticker"], []).append(c)

        # For each ticker with >=2 strategies, run fusion.
        # For single-strategy tickers, the signal is trivial.
        all_strat_signals: Dict[str, pd.Series] = {}
        ticker_strategy_map: Dict[str, List[str]] = {}

        for tk, strats in by_ticker.items():
            strat_names = []
            for i, s in enumerate(strats):
                strat_name = s.get('strategy') or f's{i}'
                sname = f"{tk}_{strat_name}"
                sig_val = 1 if s.get("direction") == "LONG" else -1
                all_strat_signals[sname] = pd.Series(
                    [sig_val, sig_val],  # at least 2 data points
                    index=["entry", "confirm"],
                )
                strat_names.append(sname)
            ticker_strategy_map[tk] = strat_names

        # Build FusionRules: one per ticker with >=2 strategies
        rules: List[Any] = []
        for tk, snames in ticker_strategy_map.items():
            if len(snames) >= 2:
                rules.append(FusionRule(strategies=snames[:3], method="majority_vote"))

        if rules:
            fused_raw = fuse_signals(all_strat_signals, rules)
            for label, series in fused_raw.items():
                val = int(series.iloc[0]) if len(series) > 0 else 0
                # Extract ticker from first strategy name in label
                first_strat = label.split("+")[0].split("_")[0]
                fused_results[first_strat] = {"signal": val, "method": "majority_vote", "n_strategies": label.count("+") + 1}

        # Add single-strategy tickers (trivial fusion)
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
        # Fallback: simple fusion
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
        step6 = StepResult(
            name="candidate_allocator",
            passed=True,
            detail=f"Allocated {n_slots} slots from {len(admitted)} candidates",
            data={"n_slots": n_slots},
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
    returns = make_synthetic_returns()
    pipeline_result: Dict[str, Any] = {"selected": [], "scorecard": None, "meta": {"n_excluded": 0}}
    try:
        from pipeline_ranker import run_pipeline
        pipeline_result = run_pipeline(
            config=config,
            candidates=candidates,
            regime_snapshot=regime_snapshot,
            returns=returns,
            now_ts=now_ts,
        )
        n_selected = len(pipeline_result.get("selected", []))
        n_excluded_meta = pipeline_result.get("meta", {}).get("n_excluded", 0)
        has_scorecard = pipeline_result.get("scorecard") is not None
        ri_in_selected = [s for s in pipeline_result.get("selected", []) if s.get("ticker") == "RI"]
        ri_filtered = len(ri_in_selected) == 0 and n_excluded_meta > 0

        step7 = StepResult(
            name="pipeline_ranker",
            passed=(n_selected > 0 and n_selected <= 3 and has_scorecard and ri_filtered),
            detail=f"selected={n_selected}, excluded={n_excluded_meta}, has_scorecard={has_scorecard}, ri_filtered={ri_filtered}",
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
            detail=f"risk_score={risk_score}, verdict={verdict}, components={len(components) if isinstance(components, dict) else 0}, nan={nan_found}",
            data={"risk_score": risk_score, "verdict": verdict, "n_components": len(components) if isinstance(components, dict) else 0},
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
            returns=returns,
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
        forecast_context = make_synthetic_forecast_context()
        enriched = augment_scorecard_with_forecast(
            pipeline_result,
            forecast_context=forecast_context,
            regime_snapshot=regime_snapshot,
        )
        has_forecast_risk = enriched.get("meta", {}).get("has_forecast_risk", False)
        adj = enriched.get("forecast_risk_adjustments")
        risk_mode = adj.get("risk_mode", "unknown") if adj else "none"

        adj_nan = False
        if adj and isinstance(adj, dict):
            for ps in adj.get("per_slot", []):
                if isinstance(ps, dict):
                    for key in ["stale", "conflict"]:
                        nested = ps.get(key, {})
                        if isinstance(nested, dict):
                            for v in nested.values():
                                if isinstance(v, float) and math.isnan(v):
                                    adj_nan = True

        bridge_ok = has_forecast_risk and not adj_nan and risk_mode in ("risk-on", "risk-off", "neutral")
        step10 = StepResult(
            name="forecast_bridge",
            passed=bridge_ok,
            detail=f"has_forecast_risk={has_forecast_risk}, risk_mode={risk_mode}, nan={adj_nan}",
            data={"has_forecast_risk": has_forecast_risk, "risk_mode": risk_mode},
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
            returns_by_ticker[tk] = returns if isinstance(returns, list) else [0.01]
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
            data={"robustness_score": robustness, "n_windows": len(wfo_result.get("windows", []))},
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
        of_result = overfit_guard_report(
            window_results=window_results,
            num_trials=10,
        )
        is_overfit = of_result.get("windows_overfit", 0) > of_result.get("windows_total", 1) / 2
        dsr = of_result.get("mean_dsr", 0)
        degradation = of_result.get("mean_degradation", 0)

        step12 = StepResult(
            name="overfit_guard",
            passed=(not is_overfit),
            detail=f"is_overfit={is_overfit}, dsr={dsr:.3f}, degradation={degradation:.3f}",
            data={"is_overfit": is_overfit, "dsr_probability": dsr, "degradation_ratio": degradation},
            elapsed_ms=(time.time() - t0) * 1000,
        )
    except Exception as exc:
        step12 = StepResult(
            name="overfit_guard", passed=False,
            detail=f"Exception: {exc}", elapsed_ms=(time.time() - t0) * 1000,
        )
        traceback.print_exc()
    results.append(step12)

    # ── Step 13: Final Verdict ──
    t0 = time.time()
    n_passed = sum(1 for r in results if r.passed)
    n_failed = sum(1 for r in results if not r.passed)
    all_passed = n_failed == 0

    final_verdict = (enriched.get("scorecard") or {}).get("verdict", "UNKNOWN")
    risk_score_final = (enriched.get("scorecard") or {}).get("risk_score", -1)

    step13 = StepResult(
        name="final_verdict",
        passed=True,
        detail=f"OVERALL: {'ALL PASSED' if all_passed else f'{n_failed} FAILED'}, "
               f"verdict={final_verdict}, risk_score={risk_score_final}",
        data={
            "all_passed": all_passed,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "n_total": len(results),
            "final_verdict": final_verdict,
            "risk_score": risk_score_final,
        },
        elapsed_ms=(time.time() - t0) * 1000,
    )
    results.append(step13)

    return _build_artifact(results, config)


def _build_artifact(results: List[StepResult], config: Dict[str, Any]) -> Dict[str, Any]:
    """Build final artifact JSON."""
    n_passed = sum(1 for r in results if r.passed)
    n_failed = sum(1 for r in results if not r.passed)
    all_passed = n_failed == 0

    final_verdict_step = next((r for r in results if r.name == "final_verdict"), None)
    final_verdict_data = final_verdict_step.data if final_verdict_step else {}
    final_verdict = final_verdict_data.get("final_verdict", "UNKNOWN")

    has_emergency = any(r.name == "EMERGENCY_STOP" for r in results)

    artifact = {
        "steps": [r.to_dict() for r in results],
        "all_passed": all_passed,
        "n_passed": n_passed,
        "n_failed": n_failed,
        "n_total": len(results),
        "final_verdict": final_verdict,
        "config_mode": config.get("mode", "unknown"),
        "has_emergency_stop": has_emergency,
        "n_live_orders": 0,
        "timestamp": time.time(),
    }

    print("=" * 70)
    print("FULL E2E DRY-RUN — strategy_combine")
    print("=" * 70)
    for r in results:
        icon = "\u2705" if r.passed else "\u274c"
        print(f"  {icon} [{r.name}] {r.detail}")
    print()
    print(f"RESULT: {'ALL PASSED' if all_passed else f'{n_failed} FAILED'}")
    print(f"  passed: {n_passed}/{len(results)}")
    print(f"  failed: {n_failed}/{len(results)}")
    print(f"  final_verdict: {final_verdict}")
    print("=" * 70)

    try:
        output_path = Path(_HERE) / "e2e_full_result.json"
        project_root = Path(_HERE).parent
        if (project_root / "config.json").exists():
            output_path = project_root / "e2e_full_result.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(artifact, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nArtifact written: {output_path}")
    except OSError as exc:
        print(f"\nWarning: could not write artifact: {exc}", file=sys.stderr)

    return artifact


def _main() -> None:
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Full E2E dry-run for strategy_combine")
    parser.add_argument("--project-root", default=None, help="Path to project root")
    args = parser.parse_args()

    if args.project_root:
        sys.path.insert(0, str(Path(args.project_root) / "code"))
        os.chdir(args.project_root)

    artifact = run_full_e2e(project_root=args.project_root)

    print("\n=== E2E Full Artifact Summary ===")
    print(json.dumps({
        "all_passed": artifact["all_passed"],
        "n_passed": artifact["n_passed"],
        "n_failed": artifact["n_failed"],
        "n_total": artifact["n_total"],
        "final_verdict": artifact["final_verdict"],
        "config_mode": artifact["config_mode"],
        "has_emergency_stop": artifact["has_emergency_stop"],
        "n_live_orders": artifact["n_live_orders"],
    }, indent=2))


if __name__ == "__main__":
    _main()
