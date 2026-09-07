"""Pipeline Ranker — единая точка входа analytics→rank→select→scorecard.

Цепочка: allocator_metrics → regime_gate → candidate_allocator.select_live_slots
         → risk_scorecard.build_scorecard + lifecycle_section.

Не импортирует broker/client, не пишет state/, не ходит в сеть.
Все функции чистые: dict-in → dict-out.

Пример использования:
    result = run_pipeline(config, candidates, regime_snapshot)
    # result["selected"] — top-≤3 слотов
    # result["scorecard"] — risk scorecard
    # result["lifecycle"] — lifecycle секция (если returns переданы)
"""
import sys
import os
from typing import Any, Dict, List, Optional

# Ensure code/ dir is on sys.path for sibling imports
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from allocator_metrics import expectancy_r, risk_penalty, regime_bonus, allocator_score
from regime_gate import admit as regime_admit
from candidate_allocator import select_live_slots, _cfg_for_allocator
from risk_scorecard_bridge import build_scorecard
from lifecycle_scorecard_ext import lifecycle_section, lifecycle_verdict
from slot_scorecard import slot_scorecard
from backtest_gate import apply_gate

# Forecast scoring (optional, fail-open)
try:
    from forecast_scorer import (
        forecast_bonus,
        forecast_risk_penalty,
        allocator_score_with_forecast,
        EXCLUDED_TICKERS as FORECAST_EXCLUDED_TICKERS,
    )
    _HAS_FORECAST = True
except ImportError:
    _HAS_FORECAST = False
    FORECAST_EXCLUDED_TICKERS = set()


def run_pipeline(
    config: dict,
    candidates: List[Dict[str, Any]],
    regime_snapshot: Optional[Dict[str, Any]] = None,
    returns: Optional[List[float]] = None,
    now_ts: Optional[float] = None,
    gate_cfg: Optional[Dict[str, Any]] = None,
    vol_pctl_map: Optional[Dict[str, float]] = None,
    forecast_results: Optional[Dict[str, Any]] = None,
    backtest_scores: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Unified pipeline: rank candidates → select ≤3 live slots → scorecard.

    config: полный config.json dict (risk, excluded, deposit_rub, ...).
    candidates: список кандидатов [{ticker, direction, win_rate, avg_win, avg_loss, ...}].
    regime_snapshot: state/regime_snapshot.json или None.
    returns: временной ряд доходностей для lifecycle (optional).
    now_ts: текущий timestamp (optional, default=time.time()).
    gate_cfg: {"enabled": bool} — regime gate config (optional).
    vol_pctl_map: {ticker: float} — ATR% перцентили (optional).
    forecast_results: {ticker: ForecastResult} — TimesFM прогнозы (optional).
        Если переданы — forecast_bonus добавляется к per_slot_scores.
        Если None — pipeline работает как раньше (backward compat).
    backtest_scores: {ticker: float} — composite_score from backtest_feedback (optional).
        Если переданы — после step 3 запускается apply_gate() для фильтрации
        selected кандидатов по backtest порогу. Если None — pipeline работает как раньше.

    Возвращает:
        {
            "selected": [...],       # ≤3 слотов (из candidate_allocator)
            "per_slot_scores": [...], # slot_scorecard для каждого selected
            "scorecard": {...},       # risk_scorecard (если слоты есть)
            "lifecycle": {...},       # lifecycle_section (если returns переданы)
            "lifecycle_verdict": str, # ALLOW / WARN
            "meta": {
                "n_candidates": int,
                "n_excluded": int,   # сколько отсечено excluded VETO
                "n_gated": int,      # сколько отсечено regime gate
                "n_selected": int,
                "max_slots": int,
                "has_forecast": bool, # True если переданы forecast_results
            },
        }
    """
    if regime_snapshot is None:
        regime_snapshot = {}
    if returns is None:
        returns = []
    if gate_cfg is None:
        gate_cfg = {"enabled": False}
    if vol_pctl_map is None:
        vol_pctl_map = {}

    # Normalize config
    risk_cfg = config.get("risk", {})
    excluded = config.get("excluded", [])
    max_slots = risk_cfg.get("max_slots", 3)
    max_contracts = risk_cfg.get("max_contracts_per_entry", 1)
    risk_per_trade_pct = risk_cfg.get("risk_per_trade_pct", 2.7)
    deposit_rub = config.get("deposit_rub", 100000)

    n_candidates = len(candidates)

    # ── Step 1: VETO excluded (before scoring, as candidate_allocator does) ──
    pre_excluded = [c for c in candidates if c.get("ticker") not in excluded]
    n_excluded = n_candidates - len(pre_excluded)

    # ── Step 2: Regime gate filter (if enabled) ──
    gate_enabled = gate_cfg.get("enabled", False)
    if gate_enabled:
        admitted = []
        for c in pre_excluded:
            vol_pctl = vol_pctl_map.get(c.get("ticker", ""), 50.0)
            result = regime_admit(c, regime_snapshot, config, vol_pctl)
            if result["admit"]:
                if result["cap_contracts"] < c.get("contracts_requested", 1):
                    c = dict(c)
                    c["contracts_requested"] = result["cap_contracts"]
                admitted.append(c)
        post_gate = admitted
    else:
        post_gate = pre_excluded
    n_gated = len(pre_excluded) - len(post_gate)

    # ── Step 3: Score + select via candidate_allocator ──
    # Rebuild cfg dict that select_live_slots expects
    allocator_cfg = {
        "excluded": excluded,
        "risk": {
            "max_slots": max_slots,
            "max_contracts_per_entry": max_contracts,
        },
        "risk_per_trade_pct": risk_per_trade_pct,
        "deposit_rub": deposit_rub,
    }

    selected = select_live_slots(post_gate, allocator_cfg, regime_snapshot)
    n_selected = len(selected)

    # ── Step 3.5: Backtest gate (if backtest_scores provided) ──
    n_backtest_rejected = 0
    if backtest_scores is not None and selected:
        gate_candidates = []
        for s in selected:
            ticker = s.get("ticker", "")
            bt_score = backtest_scores.get(ticker, 0.0)
            gate_candidates.append({
                "ticker": ticker,
                "composite_score": bt_score,
                "contracts": s.get("contracts", 1),
                "_selected_entry": s,
            })
        gate_cfg_full = {
            "min_composite_score": 0.0,
            "max_slots": max_slots,
            "max_contracts_per_entry": max_contracts,
            "excluded": excluded,
        }
        gate_result = apply_gate(gate_candidates, gate_cfg_full)
        n_backtest_rejected = len(gate_result["rejected"])
        selected = [e["_selected_entry"] for e in gate_result["passed"]]
        n_selected = len(selected)

    # ── Step 4: Per-slot scores + optional forecast enrichment ──
    risk_per_trade_rub = deposit_rub * risk_per_trade_pct / 100.0
    has_forecast = forecast_results is not None and _HAS_FORECAST
    per_slot = []
    for s in selected:
        sc = slot_scorecard(
            s, regime_snapshot=regime_snapshot,
            risk_per_trade=risk_per_trade_rub,
            returns=returns,
        )
        # Forecast enrichment: добавляем forecast_bonus в per-slot score
        if has_forecast and forecast_results:
            ticker = s.get("ticker", "")
            fr = forecast_results.get(ticker)
            fb = forecast_bonus(s, fr) if fr is not None else 0.0
            frp = forecast_risk_penalty(fr) if fr is not None else 0.0
            sc["forecast_bonus"] = fb
            sc["forecast_risk_penalty"] = frp
            # Original allocator_score без forecast
            sc["allocator_score_orig"] = s.get("score", 0.0)
        per_slot.append(sc)

    # ── Step 5: Risk scorecard (needs slots dict format for build_scorecard) ──
    # Convert selected into scorecard-compatible slot dict
    scorecard_result = None
    if selected:
        slots_for_scorecard = {}
        for s in selected:
            slot_id = "slot_%s_%d" % (s["ticker"], hash(s["ticker"]) % 10000)
            slots_for_scorecard[slot_id] = {
                "ticker": s["ticker"],
                "strategy": "pipeline_ranker",
                "contracts": s.get("contracts", 1),
                "go_rub": deposit_rub * max_contracts * risk_per_trade_pct / 100.0,
                "open_position": {
                    "direction": s.get("direction", "LONG"),
                    "qty": s.get("contracts", 1),
                    "entry_price": 0.0,
                    "entry_atr": 1.0,
                    "entry_ts": now_ts if now_ts else 0.0,
                },
                "pnl_rub": 0.0,
                "peak_pnl_rub": 0.0,
                "stop_streak": 0,
                "last_signal_ts": now_ts if now_ts else 0.0,
                "n_trades": 0,
            }

        sc_config = {
            "go_budget_rub": deposit_rub * risk_cfg.get("go_budget_pct", 50) / 100.0,
            "delta_band_pct": risk_cfg.get("delta_band_pct", 30),
            "deposit_rub": deposit_rub,
            "portfolio_stop_drawdown_pct": risk_cfg.get("portfolio_stop_drawdown_pct", 25),
            "signal_max_age_minutes": risk_cfg.get("signal_max_age_minutes", 16),
            "max_slots": max_slots,
            "max_contracts_per_entry": max_contracts,
            "excluded": excluded,
            "risk_scorecard_weights": config.get("risk_scorecard_weights", {}),
        }
        scorecard_result = build_scorecard(
            slots_for_scorecard, sc_config,
            now_ts=now_ts,
        )

    # ── Step 6: Lifecycle section (if returns provided) ──
    lifecycle_result = None
    lc_verdict = "ALLOW"
    if returns and selected:
        # Build slots dict for lifecycle
        lc_slots = {}
        for s in selected:
            slot_id = "slot_%s_%d" % (s["ticker"], hash(s["ticker"]) % 10000)
            lc_slots[slot_id] = {
                "open_position": {"direction": s.get("direction", "LONG")},
                "last_signal_ts": now_ts if now_ts else 0.0,
            }
        lifecycle_result = lifecycle_section(lc_slots, returns, now_ts)
        lc_verdict = lifecycle_verdict(lifecycle_result)

    return {
        "selected": selected,
        "per_slot_scores": per_slot,
        "scorecard": scorecard_result,
        "lifecycle": lifecycle_result,
        "lifecycle_verdict": lc_verdict,
        "meta": {
            "n_candidates": n_candidates,
            "n_excluded": n_excluded,
            "n_gated": n_gated,
            "n_selected": n_selected,
            "max_slots": max_slots,
            "has_forecast": has_forecast,
            "n_backtest_rejected": n_backtest_rejected,
        },
    }
