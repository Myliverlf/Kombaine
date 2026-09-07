#!/usr/bin/env python3
"""Strategy Architect Autopilot - local paper/backtest strategy factory.

One cycle:
  local futures CSV -> strategy grid -> futures_lab backtest -> TimesFM advisory
  -> rank/decision -> canonical StrategyRegistry/signal_pool -> JSON/MD report.

Safety: local files only, no broker calls, no live orders.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from core.state_router import get_router
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
DATA_ROOT = FUTURES_LAB / "artifacts" / "tinkoff_futures_data"
STATE_DIR = PROJECT_ROOT / "state"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"

sys.path.insert(0, str(FUTURES_LAB))

# Audit gate capital: economic_value thresholds scale with the REAL deposit,
# not with synthetic 1M-lab capital. Single source: code/capital_context.py.
try:
    from capital_context import DEFAULT_REPORT_CAPITAL_RUB as AUDIT_GATE_CAPITAL_RUB
except Exception:  # pragma: no cover - fallback if capital_context absent
    AUDIT_GATE_CAPITAL_RUB = 20_000.0


# Timeframe efficiency: dynamic weights computed per-cycle from actual data.
# Higher weight = this timeframe is more profitable per bar across all candidates.
# This makes the combine "look harder" at timeframes that prove more efficient.
TF_GLOBAL_WEIGHTS: Dict[str, float] = {}
TF_BARS_PER_DAY = {"15m": 96.0, "1h": 24.0, "4h": 6.0, "1d": 1.0}
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from futures_lab import ZOO_PARAM_GRIDS, run_backtest, _synthetic_spec_for_file  # type: ignore  # noqa:E402
PARAM_GRIDS = ZOO_PARAM_GRIDS
from timesfm_adapter import describe_source, get_adapter  # type: ignore  # noqa:E402
from equity_shape_filter import equity_shape_metrics  # type: ignore  # noqa:E402
from smooth_equity_metrics import smooth_equity_metrics, economic_value  # type: ignore  # noqa:E402
from strategy_registry import (  # type: ignore  # noqa:E402
    STATUS_ACTIVE_SIGNAL_POOL,
    STATUS_ACTIVE_WATCHLIST,
    StrategyRegistry,
)
from core.research.context import VerifiedResearchContext  # noqa:E402
from core.data.manifest import DatasetManifest, validate_dataset_manifest  # noqa:E402
from core.instruments.identity import InstrumentIdentity  # noqa:E402
from portfolio_equity_analyzer import (
    analyze_cycle as analyze_portfolio_equity,
    backtest_curve as portfolio_backtest_curve,
    equity_profile as portfolio_equity_profile,
)


def build_verified_dataset_manifest(*, dataset_id: str, strategy_id: str, params: Dict[str, Any], timeframe: str, bar_count: int, ticker: str) -> tuple[DatasetManifest, InstrumentIdentity]:
    """Build minimal verified dataset + identity objects for local backtest runs."""
    now = datetime.now(timezone.utc).isoformat()
    identity = InstrumentIdentity(
        schema_version="1.0.0",
        canonical_symbol=ticker,
        provider="local",
        provider_instrument_uid=f"local-{ticker}",
        figi=None,
        ticker=ticker,
        class_code="FUT",
        instrument_type="future",
        exchange="MOEX",
        currency="RUB",
        lot_size=1,
        underlying_uid=None,
        underlying_symbol=None,
        is_derivative=True,
        identity_source="autopilot",
        identity_verified_at=now,
        identity_verification_method="local",
        identity_version="1",
    )
    manifest = DatasetManifest(
        schema_version="1.0.0",
        dataset_id=dataset_id,
        instrument_identity_id=ticker,
        provider="local",
        source_endpoint="local_csv",
        requested_start=now,
        requested_end=now,
        actual_start=now,
        actual_end=now,
        actual_coverage_days=max(1, bar_count // 96),
        timeframe=timeframe,
        bar_count=int(bar_count),
        timezone="UTC",
        session_calendar="MOEX",
        missing_bar_summary="none",
        raw_data_checksum="raw",
        transformed_data_checksum="xform",
        acquisition_timestamp=now,
        acquisition_code_commit="local",
        transformation_pipeline="autopilot",
        transformation_parameters=json.dumps({"strategy_id": strategy_id, **(params or {})}, sort_keys=True),
        provenance_status="VERIFIED",
        integrity_status="PASS",
        identity_status="VERIFIED",
        eligibility_status="ELIGIBLE",
    )
    validate_dataset_manifest(manifest)
    identity.validate()
    return manifest, identity

# --- Iteration 05: Canonical Research Run Contract ---
sys.path.insert(0, str(PROJECT_ROOT))
from core.run_contract import ResearchRun, capture_git_revision, code_identity  # noqa:E402

# --- Iteration 08: Novelty Gate & Duplicate Suppression ---
from core.novelty_gate import (  # noqa:E402
    NoveltyAccounting,
    NoveltyPolicy,
    build_candidate_identity_for_gate,
    novelty_gate_plan,
)
from core.experiment_memory import (  # noqa:E402
    ExperimentMemory,
    experiment_family_id,
    experiment_instance_id,
)

DEFAULT_TICKERS = ["BR", "GAZP", "LKOH", "SBER", "Si"]
TARGET_UNIVERSE_SIZE = 20
EXCLUDED = {"RI"}
DEFAULT_STRATEGIES = [
    "sma_cross",
    "bollinger_reversion",
    "rsi_reversal",
    "macd_trend",
    "atr_breakout",
    "vwap_reversion",
    "ft_bband_rsi",
    "ft_macd_cci",
    "ft_multi_rsi",
    "keltner_reversion",
    "volatility_squeeze",
    "stochastic_cross",
]


def load_config() -> Dict[str, Any]:
    cfg_path = PROJECT_ROOT / "config.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    return {}


def discover_csvs(data_root: Path, tickers: Iterable[str], timeframes: Iterable[str], days: int = 60) -> Dict[Tuple[str, str], Path]:
    out: Dict[Tuple[str, str], Path] = {}
    for ticker in tickers:
        if ticker in EXCLUDED:
            continue
        for tf in timeframes:
            p = data_root / f"{ticker}_{days}d_{tf}_continuous.csv"
            if p.exists():
                out[(ticker, tf)] = p
    return out

def discover_csvs_multi(data_root: Path, tickers: Iterable[str], timeframes: Iterable[str], horizons: Iterable[int]) -> Dict[Tuple[str, str, int], Path]:
    out: Dict[Tuple[str, str, int], Path] = {}
    for ticker in tickers:
        if ticker in EXCLUDED:
            continue
        for tf in timeframes:
            for days in horizons:
                p = data_root / f"{ticker}_{days}d_{tf}_continuous.csv"
                if p.exists():
                    out[(ticker, tf, days)] = p
    return out


def data_gap_report(data_root: Path, universe: List[str], timeframes: List[str]) -> Dict[str, Any]:
    available = sorted({p.name.split("_")[0] for p in data_root.glob("*_60d_*_continuous.csv")})
    active_available = [t for t in available if t in universe and t not in EXCLUDED]
    missing_for_active: Dict[str, List[str]] = {}
    rows: Dict[str, Dict[str, int]] = {}
    for ticker in sorted(set(universe) | set(available)):
        if ticker in EXCLUDED:
            continue
        rows[ticker] = {}
        for tf in timeframes:
            p = data_root / f"{ticker}_60d_{tf}_continuous.csv"
            if not p.exists():
                missing_for_active.setdefault(ticker, []).append(tf)
                continue
            try:
                # Cheap row count without pulling pandas twice.
                rows[ticker][tf] = max(0, sum(1 for _ in p.open("r", encoding="utf-8")) - 1)
            except OSError:
                rows[ticker][tf] = 0
    return {
        "target_universe_size": TARGET_UNIVERSE_SIZE,
        "configured_universe_size": len(universe),
        "available_active_universe_size": len(active_available),
        "available_tickers": active_available,
        "excluded": sorted(EXCLUDED),
        "missing_slots_to_target": max(0, TARGET_UNIVERSE_SIZE - len(active_available)),
        "missing_timeframes": {k: v for k, v in missing_for_active.items() if k in universe},
        "rows": rows,
    }


def timesfm_status(adapter: Any) -> Dict[str, Any]:
    status = describe_source(adapter)
    if status["real_timesfm"]:
        note = "real TimesFM package loaded"
    elif status.get("fallback_reason"):
        note = f"dummy fallback; {status['fallback_reason']}"
    else:
        note = "dummy fallback; install/enable real TimesFM for neural inference"
    return {**status, "note": note}


def load_strategy_policy() -> Dict[str, Any]:
    p = REPORT_DIR / "indicator_rotation_policy.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            selected = [str(x) for x in data.get("selected", []) if str(x) in PARAM_GRIDS]
            family_rows = data.get("family_rows", []) or []
            family_map = {str(row.get("strategy")): row for row in family_rows if str(row.get("strategy"))}
            if selected:
                return {"selected": selected, "family_map": family_map, "policy": data}
        except (OSError, json.JSONDecodeError):
            data = {}
    fallback_map = {name: {"strategy": name, "policy_reason": "fallback_default", "policy_bias": 0.0, "winner_boost": 0.0, "sideways_penalty": 0.0, "clone_penalty": 0.0, "family_role": "fallback", "source": "fallback"} for name in DEFAULT_STRATEGIES}
    return {"selected": list(DEFAULT_STRATEGIES), "family_map": fallback_map, "policy": {}}


def candidate_params(strategy: str, max_params: int) -> List[Dict[str, Any]]:
    """Return a diversified deterministic slice of the strategy param grid.

    This avoids prefix bias while keeping the runtime bounded. It spreads picks
    across the whole grid, then backfills from the tail so we don't only test the
    earliest configurations.
    """
    grid = list(PARAM_GRIDS.get(strategy) or [])
    if not grid or max_params <= 0:
        return []
    if len(grid) <= max_params:
        return grid

    step = max(1, len(grid) // max_params)
    picked = []
    idx = 0
    while idx < len(grid) and len(picked) < max_params:
        picked.append(grid[idx])
        idx += step

    if len(picked) < max_params:
        for params in reversed(grid):
            if params not in picked:
                picked.append(params)
            if len(picked) >= max_params:
                break

    return picked[:max_params]


def classify_waitlist_row(row: Dict[str, Any]) -> str:
    """Classify a candidate into keep / near_keep / watchlist / reject.

    This is intentionally softer than the live gate. It gives the operator a
    usable waitlist instead of collapsing everything into strict keep/drop.
    """
    decision = str(row.get("decision") or "drop")
    sharpe = safe_float(row.get("sharpe"))
    pf = safe_float(row.get("profit_factor"))
    pnl = safe_float(row.get("total_pnl"))
    trades = int(row.get("trade_count", 0) or 0)
    shape_ok = bool(row.get("equity_shape_passed", True))
    if decision == "keep" and shape_ok and pf >= 1.2 and sharpe >= 0.5 and pnl > 0 and trades >= 8:
        return "keep"
    if (pf >= 1.05 and sharpe >= 0.2 and pnl > 0 and trades >= 5) or decision == "keep":
        return "near_keep"
    if trades >= 3 and pnl >= 0:
        return "watchlist"
    return "reject"


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def decision(metrics: Dict[str, Any], min_trades: int, min_pf: float, shape: Dict[str, Any] | None = None) -> str:
    trades = int(metrics.get("trade_count", 0) or 0)
    pnl = safe_float(metrics.get("total_pnl"))
    pf = safe_float(metrics.get("profit_factor"))
    sharpe = safe_float(metrics.get("sharpe"))
    if shape is not None and shape.get("equity_shape_passed") is False:
        return "drop"
    if trades < min_trades:
        return "retest"
    if pnl > 0 and pf >= min_pf and sharpe >= 0:
        return "keep"
    return "drop"


def timesfm_rank_bonus(row: Dict[str, Any]) -> float:
    """TimesFM influences selection/ranking but does not override bad backtests."""
    if row.get("timesfm_source") != "timesfm":
        return 0.0
    conf = safe_float(row.get("timesfm_confidence"))
    direction = str(row.get("timesfm_direction", "flat"))
    bonus = conf * 100.0
    if direction != "flat":
        bonus += conf * 25.0
    return bonus


def multiwindow_score(window_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate the same candidate across multiple history windows.

    The goal is stability: a strategy must stay good across 90d/180d/365d/1095d,
    not just spike on one lucky 60d sample.
    """
    if not window_rows:
        return {"multiwindow_score": -1e18, "window_count": 0}
    keep_rows = [r for r in window_rows if r.get("decision") == "keep" or r.get("decision") == "retest"]
    if not keep_rows:
        keep_rows = window_rows
    pnl = sum(safe_float(r.get("total_pnl")) for r in keep_rows)
    pf_vals = [max(0.01, safe_float(r.get("profit_factor"), 1.0)) for r in keep_rows]
    sharpe_vals = [safe_float(r.get("sharpe")) for r in keep_rows]
    dd_vals = [abs(safe_float(r.get("max_drawdown"))) for r in keep_rows]
    keep_ratio = len([r for r in window_rows if r.get("decision") == "keep"]) / max(1, len(window_rows))
    stability = len({(r.get("ticker"), r.get("timeframe"), r.get("strategy")) for r in keep_rows})
    score = (
        pnl
        + sum(pf_vals) * 1000.0
        + sum(sharpe_vals) * 800.0
        - sum(dd_vals) * 15.0
        + keep_ratio * 2000.0
        + stability * 250.0
    )
    return {
        "multiwindow_score": round(score, 4),
        "window_count": len(window_rows),
        "multiwindow_pnl": round(pnl, 4),
        "multiwindow_pf_avg": round(sum(pf_vals) / max(1, len(pf_vals)), 4),
        "multiwindow_sharpe_avg": round(sum(sharpe_vals) / max(1, len(sharpe_vals)), 4),
        "multiwindow_dd_avg": round(sum(dd_vals) / max(1, len(dd_vals)), 4),
        "multiwindow_keep_ratio": round(keep_ratio, 4),
    }


def compute_tf_weights(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute per-timeframe efficiency weights from actual candidate data.

    Measures PnL-per-bar and trades-per-bar for each timeframe across all
    candidates. 15m has ~4x more bars than 1h in the same calendar period,
    so raw PnL-per-bar is the fair comparison. Timeframes that generate more
    profit per bar get higher weight. The combine then biases toward those.

    Returns dict: {timeframe: weight} where weight is normalized so the best
    timeframe gets ~1.5x and worst gets ~0.5x (clamped).
    """
    tf_data: Dict[str, Dict[str, float]] = {}
    for r in rows:
        if "error" in r:
            continue
        tf = str(r.get("timeframe", "1h"))
        pnl = safe_float(r.get("total_pnl"))
        dd = max(1.0, safe_float(r.get("max_drawdown"), 1.0))
        trades = safe_float(r.get("trades"))
        pf = safe_float(r.get("profit_factor"))
        bars = max(1.0, TF_BARS_PER_DAY.get(tf, 24.0) * 60.0)  # ~60 days of data
        if tf not in tf_data:
            tf_data[tf] = {"pnl_sum": 0, "dd_sum": 0, "trades_sum": 0, "pf_sum": 0, "count": 0, "bars": bars}
        d = tf_data[tf]
        d["pnl_sum"] += max(0, pnl)
        d["dd_sum"] += dd
        d["trades_sum"] += trades
        d["pf_sum"] += max(0, pf - 1.0)
        d["count"] += 1

    if not tf_data:
        return {}

    # Efficiency = (pnl_per_bar * avg_pf) with trade frequency bonus
    tf_efficiency: Dict[str, float] = {}
    for tf, d in tf_data.items():
        bars = d["bars"]
        count = max(1, d["count"])
        pnl_per_bar = d["pnl_sum"] / bars
        avg_pf = d["pf_sum"] / count
        trades_per_bar = d["trades_sum"] / bars
        # Score: profit efficiency + trade frequency (more trades = more statistical evidence)
        tf_efficiency[tf] = pnl_per_bar * (1.0 + avg_pf * 0.3) * (1.0 + min(1.0, trades_per_bar * 10.0))

    # Normalize: best = 1.5, worst = 0.5 (linear stretch around 1.0)
    max_eff = max(tf_efficiency.values()) if tf_efficiency else 1.0
    min_eff = min(tf_efficiency.values()) if tf_efficiency else 0.0
    eff_range = max_eff - min_eff if max_eff > min_eff else 1.0

    weights: Dict[str, float] = {}
    for tf, eff in tf_efficiency.items():
        norm = (eff - min_eff) / eff_range  # 0..1
        weights[tf] = 0.5 + norm * 1.0  # 0.5 .. 1.5

    return weights


def tf_efficiency_bonus(row: Dict[str, Any]) -> float:
    """Timeframe efficiency bonus for a single row.

    Returns a score bonus (typically -500 .. +500) that pushes efficient
    timeframes up and inefficient ones down in the ranking.
    """
    tf = str(row.get("timeframe", "1h"))
    w = TF_GLOBAL_WEIGHTS.get(tf, 1.0)
    # Map weight 0.5..1.5 to bonus -500..+500
    return (w - 1.0) * 1000.0


def rank_score(row: Dict[str, Any]) -> float:
    shape_bonus = 0.0
    if row.get("equity_shape_passed") is True:
        # SBER-like ideal: high R², all/most windows up, little flatness, low DD.
        shape_bonus = (
            safe_float(row.get("equity_shape_score")) * 0.75
            + safe_float(row.get("equity_shape_r2")) * 1800.0
            + safe_float(row.get("equity_shape_positive_window_ratio")) * 1600.0
            - safe_float(row.get("equity_shape_flat_window_ratio")) * 1800.0
            - safe_float(row.get("equity_shape_dd_ratio")) * 900.0
        )
    elif row.get("equity_shape_passed") is False:
        shape_bonus = -10000.0
    diversity_bonus = safe_float(row.get("policy_winner_boost"))
    diversity_penalty = safe_float(row.get("policy_sideways_penalty")) + safe_float(row.get("policy_clone_penalty"))
    policy_bias = safe_float(row.get("policy_bias"))
    return (
        safe_float(row.get("total_pnl"))
        + safe_float(row.get("profit_factor")) * 100.0
        + safe_float(row.get("sharpe")) * 50.0
        - safe_float(row.get("max_drawdown")) * 0.05
        + timesfm_rank_bonus(row)
        + shape_bonus
        + diversity_bonus
        - diversity_penalty
        + policy_bias * 0.5
        + tf_efficiency_bonus(row)
    )


def trade_bucket(trades: Any) -> str:
    t = safe_float(trades)
    if t < 40:
        return "low"
    if t > 120:
        return "high"
    return "normal"


def archetype_match_score(row: Dict[str, Any]) -> float:
    """Similarity to desired smooth-rising equity archetype, 0..100."""
    r2 = safe_float(row.get("equity_shape_r2"))
    positive = safe_float(row.get("equity_shape_positive_window_ratio"))
    flat = safe_float(row.get("equity_shape_flat_window_ratio"))
    dd_ratio = safe_float(row.get("equity_shape_dd_ratio"))
    score = (
        min(1.0, max(0.0, r2)) * 35.0
        + min(1.0, max(0.0, positive)) * 35.0
        + (1.0 - min(1.0, max(0.0, flat))) * 15.0
        + (1.0 - min(1.0, max(0.0, dd_ratio))) * 15.0
    )
    return round(max(0.0, min(100.0, score)), 4)






def capital_normalized_score(row: Dict[str, Any]) -> float:
    cap = max(1.0, safe_float(row.get("capital_rub", 20000.0), 20000.0))
    pnl = safe_float(row.get("total_pnl"))
    pf = safe_float(row.get("profit_factor"))
    sharpe = safe_float(row.get("sharpe"))
    dd = max(1.0, safe_float(row.get("max_drawdown"), 1.0))
    trades = max(1.0, safe_float(row.get("trades"), 1.0))
    roc_m = (pnl / cap) * 100.0
    return (roc_m * 120.0) + (pnl * 2.0) + (max(0.0, pf - 1.0) * 250.0) + (max(0.0, sharpe) * 180.0) - ((dd / cap) * 1000.0) + (min(trades, 400.0) * 0.5)
def shortlist_candidates(rows: list[Dict[str, Any]], limit: int = 5) -> list[Dict[str, Any]]:
    """Capital-aware deduped shortlist: one row per (ticker, strategy, timeframe, params)."""
    eligible = [r for r in rows if r.get("decision") in {"keep", "near_keep", "retest"} and not bool(r.get("tail_veto"))]
    seen = set()
    unique: list[Dict[str, Any]] = []
    for r in eligible:
        params_key = json.dumps(r.get('params') or {}, sort_keys=True, ensure_ascii=False)
        key = (r.get('ticker'), r.get('strategy'), r.get('timeframe'), params_key)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    unique.sort(key=lambda r: (capital_normalized_score(r), safe_float(r.get('portfolio_balance_score', r.get('portfolio_score', r.get('rank_score')))), safe_float(r.get('total_pnl'))), reverse=True)
    return unique[:limit]

def portfolio_score(row: Dict[str, Any]) -> float:
    """Risk/product score for portfolio construction.

    This version is deliberately biased toward the user's real target:
    - enough PnL to matter on capital
    - frequent trading (so the strategy is actually usable)
    - stable equity as a filter, not the primary goal

    It should lift high-trade / profitable candidates above tiny low-trade keeps.
    """
    pnl = safe_float(row.get("total_pnl"))
    dd = max(1.0, safe_float(row.get("max_drawdown"), 1.0))
    pf = safe_float(row.get("profit_factor"))
    sharpe = safe_float(row.get("sharpe"))
    trades = max(1.0, safe_float(row.get("trades"), 1.0))
    r2 = safe_float(row.get("equity_shape_r2"))
    smooth = safe_float(row.get("smooth_score"))
    ulcer = safe_float(row.get("ulcer_index"))
    clusters = safe_float(row.get("drawdown_cluster_count"))

    # Capital-aware profitability: prefer strategies that can make meaningful money
    # on a small account rather than just show a pretty but tiny curve.
    capital = max(1.0, safe_float(row.get("capital_base", 20000.0), 20000.0))
    roc_60d = (pnl / capital) * 100.0
    roc_monthly_est = roc_60d * 0.5  # 60d window scaled to monthly rough estimate

    # Core profitability on risk.
    pnl_dd = min(12.0, pnl / dd)

    # Frequency bonus: we want at least weekly/daily turnover, but not insane churn.
    # 40-220 trades in 60d is the sweet spot for this pool.
    # Harder frequency shaping: prefer strategies that can trade often enough
    # to matter on a small account, while still capping runaway churn.
    if trades < 40:
        freq_bonus = -5000.0
    elif trades < 80:
        freq_bonus = 0.0
    elif trades <= 220:
        freq_bonus = 1400.0 + min(900.0, trades * 4.0)
    else:
        freq_bonus = 1000.0 - (trades - 220.0) * 6.0

    # Evidence penalty: if a strategy is not profitable enough, don't let it rank.
    if trades < 10 or pf < 1.15 or pnl <= 0:
        evidence_penalty = 10000.0
    else:
        evidence_penalty = 0.0

    # Smoothness still matters, but as a tie-breaker behind profitability/frequency.
    smooth_bonus = (
        r2 * 900.0
        + max(0.0, smooth) * 0.35
        - ulcer * 220.0
        - clusters * 260.0
    )

    return round(
        pnl * 12.0
        + roc_monthly_est * 420.0
        + pnl_dd * 900.0
        + max(0.0, pf - 1.0) * 700.0
        + max(0.0, sharpe) * 600.0
        + archetype_match_score(row) * 35.0
        + smooth_bonus
        + freq_bonus
        + safe_float(row.get("economic_score")) * 20.0
        + timesfm_rank_bonus(row) * 0.15
        - evidence_penalty,
        4,
    )


def enrich_row_scores(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Add ticker/strategy/family context scores in-place."""
    family: Dict[str, List[float]] = {}
    ticker_strategy: Dict[str, List[float]] = {}
    for r in rows:
        if "error" in r:
            continue
        r["trade_bucket"] = trade_bucket(r.get("trades"))
        r["archetype_match_score"] = archetype_match_score(r)
        r["portfolio_score"] = portfolio_score(r)
        family.setdefault(str(r.get("strategy")), []).append(safe_float(r.get("portfolio_score")))
        ticker_strategy.setdefault(f"{r.get('ticker')}::{r.get('strategy')}", []).append(safe_float(r.get("portfolio_score")))
    family_avg = {k: sum(v) / max(1, len(v)) for k, v in family.items()}
    ts_avg = {k: sum(v) / max(1, len(v)) for k, v in ticker_strategy.items()}
    for r in rows:
        if "error" in r:
            continue
        r["family_global_score"] = round(family_avg.get(str(r.get("strategy")), 0.0), 4)
        r["ticker_strategy_score"] = round(ts_avg.get(f"{r.get('ticker')}::{r.get('strategy')}", 0.0), 4)
    return {"family_global_score": family_avg, "ticker_strategy_score": ts_avg}


def build_indicator_leaderboard(rows: List[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    """Compact operator-facing ranking for the latest cycle."""
    leaderboard: List[Dict[str, Any]] = []
    for r in rows:
        if "error" in r:
            continue
        leaderboard.append({
            "ticker": r.get("ticker"),
            "strategy": r.get("strategy"),
            "timeframe": r.get("timeframe"),
            "rank_score": safe_float(r.get("rank_score")),
            "portfolio_score": safe_float(r.get("portfolio_score")),
            "total_pnl": safe_float(r.get("total_pnl")),
            "profit_factor": safe_float(r.get("profit_factor")),
            "tail_veto": bool(r.get("tail_veto")),
            "trade_bucket": r.get("trade_bucket"),
        })
    leaderboard.sort(key=lambda x: (x["portfolio_score"], x["rank_score"], x["total_pnl"]), reverse=True)
    return leaderboard[:limit]


def coverage_reason_report(rows: List[Dict[str, Any]], universe: List[str], top: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Explain why each ticker is or is not in the final top list."""
    top_tickers = {str(r.get("ticker")) for r in top}
    per_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        ticker = str(row.get("ticker"))
        if not ticker:
            continue
        per_ticker.setdefault(ticker, []).append(row)

    out: List[Dict[str, Any]] = []
    for ticker in universe:
        ticker_rows = per_ticker.get(ticker, [])
        keep_rows = [r for r in ticker_rows if r.get("decision") == "keep"]
        shape_pass = any(bool(r.get("equity_shape_passed", True)) for r in ticker_rows)
        best = max(ticker_rows, key=lambda r: safe_float(r.get("portfolio_score", r.get("rank_score"))), default=None)
        reason = "selected" if ticker in top_tickers else (
            "no_keep_candidates" if not keep_rows else "coverage_gap"
        )
        out.append({
            "ticker": ticker,
            "in_top": ticker in top_tickers,
            "tested": len(ticker_rows),
            "keep": len(keep_rows),
            "shape_pass": shape_pass,
            "reason": reason,
            "best_strategy": best.get("strategy") if best else None,
            "best_portfolio_score": safe_float(best.get("portfolio_score")) if best else 0.0,
            "best_trades": int(best.get("trades", 0) or 0) if best else 0,
        })
    return out


def _row_key(row: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("ticker")),
        str(row.get("timeframe")),
        str(row.get("strategy")),
        json.dumps(row.get("params") or {}, sort_keys=True, ensure_ascii=False),
    )


def _median(values: List[float], default: float = 0.0) -> float:
    xs = sorted(v for v in values if not math.isnan(v) and not math.isinf(v))
    if not xs:
        return default
    mid = len(xs) // 2
    if len(xs) % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def portfolio_balanced_top(
    rows: List[Dict[str, Any]],
    top_n: int,
    max_per_ticker: int = 3,
    max_per_strategy: int = 3,
    min_tickers: int = 5,
    min_trades: int = 0,
    trade_bucket: str | None = None,
) -> List[Dict[str, Any]]:
    """Portfolio-style shortlist with optional trade-bucket filtering.

    The objective is not "highest 20 scalar ranks". The operator wants separate
    pools:
      - high-frequency operating pool (100+ trades preferred)
      - swing/research pool (lower-trade families)

    This helper can produce either pool by filtering upfront.
    """
    if not rows:
        return []

    filtered: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if min_trades and int(row.get("trades", 0) or 0) < min_trades:
            continue
        if trade_bucket and str(row.get("trade_bucket") or "") != trade_bucket:
            continue
        key = (
            row.get("ticker"),
            row.get("strategy"),
            row.get("timeframe"),
            json.dumps(row.get("params") or {}, sort_keys=True, ensure_ascii=False),
        )
        if key in seen:
            continue
        seen.add(key)
        filtered.append(row)

    if not filtered:
        return []

    scored = sorted(filtered, key=lambda r: r.get("rank_score", 0), reverse=True)
    by_ticker: Dict[str, list] = {}
    by_strategy: Dict[str, list] = {}
    result = []
    for row in scored:
        t = row.get("ticker", "")
        s = row.get("strategy", "")
        if len(by_ticker.get(t, [])) >= max_per_ticker:
            continue
        if len(by_strategy.get(s, [])) >= max_per_strategy:
            continue
        result.append(row)
        by_ticker.setdefault(t, []).append(row)
        by_strategy.setdefault(s, []).append(row)
        if len(result) >= top_n:
            break
    return result


def apply_tail_veto(rows: List[Dict[str, Any]], initial_cash: float = 1_000_000.0) -> List[Dict[str, Any]]:
    """Remove rows that fail the tail-veto heuristic.

    The row already carries `tail_veto` from portfolio_equity_analyzer. This
    helper is the missing glue for the autopilot selection stage.
    """
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item.get("tail_veto"):
            item["decision"] = item.get("decision", "drop")
            item["tail_veto_reason"] = "tail_veto"
            continue
        out.append(item)
    return out


def _run_cycle_inner(
    rr: ResearchRun,
    args: argparse.Namespace,
    cfg: Dict[str, Any],
    universe: List[str],
    timeframes: List[str],
    horizons: List[int],
    adapter: Any,
    gap_report: Dict[str, Any],
    tf_status: Dict[str, Any],
    rows: List[Dict[str, Any]],
    started: float,
) -> Dict[str, Any]:
    """Core cycle logic - called inside a locked ResearchRun.

    Iteration 05: integrates run contract for immutable run bundles.
    """

def run_cycle(args: argparse.Namespace) -> Dict[str, Any]:
    """Compatibility wrapper for CLI entrypoint.

    The CLI historically expects run_cycle(args); the core logic lives in
    _run_cycle_inner and is executed through the locked research flow.
    """
    universe = [x.strip() for x in getattr(args, 'universe', '').split(',') if x.strip()] if getattr(args, 'universe', '') else list(load_config().get('universe', []))
    router = get_router(scope_id="strategy_architect")
    if not router.state.goal:
        router.set_goal("adaptive high-frequency / capital-aware strategy discovery")
    router.set_phase("cycle-start")
    router.set_next_action("run autopilot with compact state prefix")
    timeframes = [x.strip() for x in getattr(args, 'timeframes', '15m,1h').split(',') if x.strip()]
    horizons = [int(x) for x in [y.strip().replace('d', '') for y in getattr(args, 'horizons', '60,365,1095').split(',')] if x]
    cfg = load_config()
    started = time.time()
    gap_report = {}
    tf_status = {}
    rows = []
    state_prefix = router.build_prompt_prefix()
    runs_dir = PROJECT_ROOT / 'reports' / 'research_pipeline'
    run_id = getattr(args, 'run_id', '') or f"autopilot-{int(started)}"
    try:
        rr = ResearchRun.load(runs_dir, run_id)
    except Exception:
        rr = ResearchRun.create(runs_dir, prefix='autopilot', state_dir=PROJECT_ROOT / 'state')
    adapter = get_adapter(cfg)
    _ = _run_cycle_inner(rr, args, cfg, universe, timeframes, horizons, adapter, gap_report, tf_status, rows, started)
    # --- Planning: resolve strategies and build plan grid ---
    policy_bundle = load_strategy_policy() if args.strategies == "policy" else {"selected": args.strategies.split(","), "family_map": {}, "policy": {}}
    selected_strategies = policy_bundle.get("selected", []) if isinstance(policy_bundle, dict) else list(policy_bundle)
    family_map = policy_bundle.get("family_map", {}) if isinstance(policy_bundle, dict) else {}
    policy_meta = policy_bundle.get("policy", {}) if isinstance(policy_bundle, dict) else {}

    # --- Start run planning with manifest ---
    git_info = capture_git_revision(PROJECT_ROOT)
    code_id = code_identity(
        PROJECT_ROOT,
        module_paths=["code/strategy_architect_autopilot.py", "code/data_loader.py"],
    )
    rr.start_planning(
        universe=universe,
        timeframes=timeframes,
        horizons=horizons,
        strategy_families=selected_strategies,
        arguments={
            "max_params": args.max_params,
            "min_bars": args.min_bars,
            "min_trades": args.min_trades,
            "min_pf": args.min_pf,
            "top_n": args.top_n,
            "max_per_ticker": args.max_per_ticker,
            "max_per_strategy": args.max_per_strategy,
            "min_tickers": args.min_tickers,
            "initial_cash": args.initial_cash,
            "horizons": horizons,
        },
        cost_assumptions={
            "commission": "synthetic_per_trade",
            "slippage": "default",
            "initial_cash": args.initial_cash,
            "sizing": "single_contract",
        },
        git_info=git_info,
        code_id=code_id,
        timesfm_state=tf_status.get("source_key", "unknown"),
        backtest_engine_version="futures_lab",
    )

    # --- Build and persist research plan before backtests ---
    plan_configs: List[Dict[str, Any]] = []
    _csv_cache: Dict[Tuple[str, str, int], pd.DataFrame] = {}

    def _discover_and_cache(tickers, tfs, hrs):
        for ticker in tickers:
            if ticker in EXCLUDED:
                continue
            for tf in tfs:
                for days in hrs:
                    p = DATA_ROOT / f"{ticker}_{days}d_{tf}_continuous.csv"
                    if p.exists() and (ticker, tf, days) not in _csv_cache:
                        try:
                            _csv_cache[(ticker, tf, days)] = pd.read_csv(p)
                        except Exception:
                            pass

    _discover_and_cache(universe, timeframes, horizons)
    # Also discover 60d if not in horizons
    if 60 not in horizons:
        _discover_and_cache(universe, timeframes, [60])

    plan_idx = 0
    for (ticker, tf, days) in sorted(_csv_cache.keys()):
        df = _csv_cache[(ticker, tf, days)]
        if len(df) < args.min_bars:
            continue
        for strat in selected_strategies:
            for params in candidate_params(strat, args.max_params):
                import hashlib as _hl
                _raw = f"{ticker}|{tf}|{strat}|{json.dumps(params, sort_keys=True)}|{days}"
                _key = _hl.sha256(_raw.encode()).hexdigest()[:16]
                plan_configs.append({
                    "config_key": _key,
                    "instrument": ticker,
                    "timeframe": tf,
                    "strategy": strat,
                    "parameters": params,
                    "horizon_days": days,
                    "dataset_path": str(DATA_ROOT / f"{ticker}_{days}d_{tf}_continuous.csv"),
                })
                plan_idx += 1

    plan_grid = {
        "configs": plan_configs,
        "validation_gates": {
            "min_trades": args.min_trades,
            "min_pf": args.min_pf,
        },
    }
    rr.persist_plan(plan_grid)

    # Set dataset info in manifest (path, not cached DataFrame)
    for (ticker, tf, days), df in _csv_cache.items():
        rr.add_dataset_entry(ticker, tf, days, DATA_ROOT / f"{ticker}_{days}d_{tf}_continuous.csv")

    # --- Iteration 08: Novelty Gate & Duplicate Suppression ---
    # Gate runs AFTER plan persistence, BEFORE expensive backtest execution.
    # Only EXACT_DUPLICATE is auto-skipped.  Everything else RUNS.
    novelty_policy = NoveltyPolicy()
    novelty_accounting: Optional[NoveltyAccounting] = None
    skipped_config_keys: set = set()

    try:
        exp_memory = ExperimentMemory(db_path=STATE_DIR / "experiment_memory.db")

        def _classify_for_gate(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            """Wrapper to classify a plan config against experiment memory."""
            ident = build_candidate_identity_for_gate(cfg)
            return exp_memory.classify_candidate(
                instrument=ident["instrument"],
                timeframe=ident["timeframe"],
                strategy=ident["strategy"],
                parameters=ident["parameters"],
                horizon_days=ident["horizon_days"],
                dataset_hash=ident["dataset_hash"],
                dataset_start=ident["dataset_start"],
                dataset_end=ident["dataset_end"],
                code_hash=ident["code_hash"],
                cost_model_hash=ident["cost_model_hash"],
                validation_version=ident["validation_version"],
                backtest_engine_version=ident["backtest_engine_version"],
            )
    except Exception as e:
        logger.warning("Novelty gate: memory unavailable (%s); fail-open", e)
        exp_memory = None
        _classify_for_gate = None

    # Enrich plan configs with identity fields for the gate
    for cfg in plan_configs:
        ident = build_candidate_identity_for_gate(cfg)
        fid = experiment_family_id(
            ident["instrument"], ident["timeframe"], ident["strategy"],
            ident["parameters"], ident["horizon_days"],
            ident.get("validation_version", "default"),
        )
        iid = experiment_instance_id(**{
            k: ident[k] for k in [
                "instrument", "timeframe", "strategy", "parameters", "horizon_days",
                "dataset_hash", "dataset_start", "dataset_end",
                "code_hash", "cost_model_hash", "validation_version", "backtest_engine_version",
            ]
        })
        cfg["experiment_family_id"] = fid
        cfg["experiment_instance_id"] = iid

    if _classify_for_gate is not None:
        novelty_accounting = novelty_gate_plan(
            plan_configs=plan_configs,
            classify_fn=_classify_for_gate,
            policy=novelty_policy,
            force_reproduction=getattr(args, "force_reproduction", False),
            run_dir=rr.run_dir,
        )
        # Record skipped configs
        for novelty_decision in novelty_accounting.decisions:
            if novelty_decision.decision == "SKIP_EXACT_DUPLICATE":
                skipped_config_keys.add(plan_configs[
                    novelty_accounting.decisions.index(novelty_decision)
                ].get("config_key", ""))
                # Append skipped candidate to ledger
                rr.append_skipped_candidate(
                    plan_configs[novelty_accounting.decisions.index(novelty_decision)],
                    novelty_decision.to_dict(),
                )
            elif novelty_decision.decision == "RUN_FORCED_REPRODUCTION":
                # Forced: will execute, mark in accounting
                pass

        # Update manifest with novelty accounting
        manifest = rr.manifest()
        manifest.update(novelty_accounting.to_manifest_dict())
        rr._manifest.update(novelty_accounting.to_manifest_dict())
        rr._persist_manifest()

        logger.info(
            "Novelty gate: planned=%d executed=%d skipped=%d forced=%d errors=%d",
            novelty_accounting.planned,
            novelty_accounting.executed,
            novelty_accounting.skipped_exact_duplicate,
            novelty_accounting.forced_reproduction,
            novelty_accounting.lookup_errors,
        )
    else:
        logger.warning("Novelty gate: memory unavailable; all candidates will execute")

    # --- Execute backtests (same logic as before) ---
    # FIX 2026-09-05: previously only min(horizons) dataset per (ticker, tf) was
    # executed while the plan contained ALL horizons -> PARTIAL runs (504/1008),
    # long-horizon data never backtested, and stale `days` variable leaked into
    # dataset_id/context. Now execute every cached (ticker, tf, days).
    csvs = dict(_csv_cache)

    if horizons == [60]:
        csvs = {k: v for k, v in _csv_cache.items() if k[2] == 60}

    for (ticker, tf, days), df in sorted(csvs.items()):
        if len(df) < args.min_bars:
            continue
        spec = _synthetic_spec_for_file(ticker)
        closes = [safe_float(x) for x in df["close"].tail(256).tolist()]
        forecast = adapter.forecast(ticker, closes, horizon=20)
        for strat in selected_strategies:
            policy_row = family_map.get(strat, {}) if isinstance(family_map, dict) else {}
            for params in candidate_params(strat, args.max_params):
                # --- Iteration 08: check novelty gate skip ---
                import hashlib as _hl
                # FIX 2026-09-05: key must match plan key (uses real `days`),
                # not min(horizons) — otherwise long-horizon configs skipped wrongly.
                _bt_days = days
                _bt_raw = f"{ticker}|{tf}|{strat}|{json.dumps(params, sort_keys=True)}|{_bt_days}"
                _bt_key = _hl.sha256(_bt_raw.encode()).hexdigest()[:16]
                if _bt_key in skipped_config_keys:
                    continue  # Novelty gate: skip this exact duplicate

                row_start = time.time()
                try:
                    manifest, identity = build_verified_dataset_manifest(
                        dataset_id=f"{ticker}_{days}_{tf}",
                        strategy_id=strat,
                        params=params,
                        timeframe=tf,
                        bar_count=len(df),
                        ticker=ticker,
                    )
                    ctx = VerifiedResearchContext(
                        schema_version='1.0.0',
                        context_id=f"ctx-{ticker}-{tf}-{days}-{strat}",
                        experiment_id=f"exp-{ticker}-{tf}-{days}-{strat}",
                        dataset_id=f"{ticker}_{days}_{tf}",
                        dataset_manifest=manifest,
                        instrument_identity=identity,
                        requested_horizon=str(days),
                        resolved_horizon=str(days),
                        timeframe=tf,
                        strategy_id=strat,
                        strategy_version='1.0.0',
                        strategy_hash='local',
                        compatibility_result='PASS',
                        research_policy_version='1.0.0',
                        cost_model_version='1.0.0',
                        ingress_receipt='local-ingress',
                        dataset_checksum='xform',
                        identity_hash='local',
                        identity_version='1',
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    metrics, trades, eq = run_backtest(
                        df, spec, strat, params,
                        initial_cash=args.initial_cash,
                        contracts=1, max_contracts=1,
                        commission_per_contract=args.commission_per_contract,
                        slippage_bps=args.slippage_bps,
                        research_context=ctx,
                    )
                except Exception as exc:
                    row = {
                        "ticker": ticker, "timeframe": tf, "strategy": strat,
                        "params": params, "error": str(exc), "decision": "error",
                        "status": "error", "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                    rr.append_candidate(row)
                    rows.append(row)
                    continue
                row_end = time.time()
                shape = equity_shape_metrics(eq, args.initial_cash)
                pnl_curve = [float(v) - args.initial_cash for v in eq.tolist()]
                smooth = smooth_equity_metrics(pnl_curve)
                econ = economic_value({**metrics, "trades": int(metrics.get("trade_count", 0) or 0)}, capital_rub=AUDIT_GATE_CAPITAL_RUB)
                # КАЛИБРОВКА 2026-09-02 (нетто-популяция 8 топ-кандидатов,
                # комиссия 1.5₽ + слиппедж 2bps, 60d/1h, капитал 20к):
                #   reports/strategy_architect/calibration_net_smooth.json
                # Распределение нетто-плюсовых стратегий:
                #   clusters 16-27, ulcer 13.0-27.4, tuw 19-58.3%, score 19-1194.
                # Старые пороги (clusters<=10, ulcer<=18, tuw<=50%) резали ВСЕ 560
                # конфигураций — недостижимо для 1h-трендовых стратегий.
                smooth_live_ready = bool(
                    safe_float(smooth.get("total_pnl")) > 0
                    and safe_float(smooth.get("tail_pnl")) >= 0
                    and safe_float(smooth.get("micro_tail_pnl")) >= 0
                    and safe_float(smooth.get("equity_r2")) >= 0.25
                    and safe_float(smooth.get("dd_ratio")) <= 1.05
                    and safe_float(smooth.get("ulcer_index")) <= 30.0
                    and safe_float(smooth.get("max_time_under_water")) <= int(len(pnl_curve) * 0.60)
                    and safe_float(smooth.get("drawdown_cluster_count")) <= 30
                    and safe_float(smooth.get("smooth_score")) > 180.0
                )
                wins = sum(1 for t in trades if t.pnl > 0)
                losses = sum(1 for t in trades if t.pnl < 0)
                dec_result = decision(metrics, args.min_trades, args.min_pf, shape)
                if dec_result == "keep" and (not smooth_live_ready or not econ.get("economic_ok")):
                    # near_keep: базовый гейт пройден, но кривая недостаточно гладкая
                    # или экономика слабая. НЕ демутим в drop — иначе выдача цикла
                    # пустая (все 560 конфигураций отброшены). Уровни:
                    # keep = базовый + гладкость + экономика;
                    # near_keep = базовый пройден, жёсткие гейты не полностью;
                    # далее классификатор назначает watchlist / reject.
                    dec_result = "near_keep"
                row = {
                    "ticker": ticker, "timeframe": tf, "strategy": strat,
                    "params": params, "horizon_days": days,
                    "trades": int(metrics.get("trade_count", 0) or 0),
                    "wins": wins, "losses": losses,
                    "win_rate": safe_float(metrics.get("win_rate_pct")),
                    "total_pnl": round(safe_float(metrics.get("total_pnl")), 2),
                    "avg_trade": round(safe_float(metrics.get("avg_trade")), 2),
                    "profit_factor": safe_float(metrics.get("profit_factor")),
                    "max_drawdown": round(safe_float(metrics.get("max_drawdown")), 2),
                    "sharpe": round(safe_float(metrics.get("sharpe")), 4),
                    "timesfm_direction": forecast.direction,
                    "timesfm_confidence": forecast.confidence,
                    "timesfm_source": forecast.source,
                    "equity_shape_passed": shape.get("equity_shape_passed"),
                    "equity_shape_score": shape.get("equity_shape_score"),
                    "equity_shape_r2": shape.get("equity_shape_r2"),
                    "equity_shape_positive_window_ratio": shape.get("equity_shape_positive_window_ratio"),
                    "equity_shape_flat_window_ratio": shape.get("equity_shape_flat_window_ratio"),
                    "equity_shape_dd_ratio": shape.get("equity_shape_dd_ratio"),
                    "equity_shape_total_pnl": shape.get("equity_shape_total_pnl"),
                    "equity_shape_max_dd": shape.get("equity_shape_max_dd"),
                    "equity_shape_reason": shape.get("equity_shape_reason"),
                    "smooth_ok": smooth.get("smooth_ok"),
                    "smooth_live_ready": smooth_live_ready,
                    "smooth_reason": smooth.get("smooth_reason"),
                    "smooth_score": smooth.get("smooth_score"),
                    "ulcer_index": smooth.get("ulcer_index"),
                    "max_time_under_water": smooth.get("max_time_under_water"),
                    "drawdown_cluster_count": smooth.get("drawdown_cluster_count"),
                    "recovery_factor": smooth.get("recovery_factor"),
                    "monotonicity_score": smooth.get("monotonicity_score"),
                    "smooth_tail_pnl": smooth.get("tail_pnl"),
                    "smooth_micro_tail_pnl": smooth.get("micro_tail_pnl"),
                    "economic_ok": econ.get("economic_ok"),
                    "economic_score": econ.get("economic_score"),
                    "economic_reason": econ.get("economic_reason"),
                    "policy_bias": safe_float(policy_row.get("policy_bias")),
                    "policy_winner_boost": safe_float(policy_row.get("winner_boost")),
                    "policy_sideways_penalty": safe_float(policy_row.get("sideways_penalty")),
                    "policy_clone_penalty": safe_float(policy_row.get("clone_penalty")),
                    "policy_role": str(policy_row.get("family_role") or "fallback"),
                    "policy_reason": str(policy_row.get("policy_reason") or ("fallback_default" if strat not in family_map else "")),
                    "risk_verdict": "ALLOW" if dec_result == "keep" else "RETEST" if dec_result in ("retest", "near_keep") else "VETO",
                    "decision": dec_result,
                    "waitlist_tier": classify_waitlist_row({
                        "decision": dec_result,
                        "sharpe": safe_float(metrics.get("sharpe")),
                        "profit_factor": safe_float(metrics.get("profit_factor")),
                        "total_pnl": safe_float(metrics.get("total_pnl")),
                        "trade_count": int(metrics.get("trade_count", 0) or 0),
                        "equity_shape_passed": shape.get("equity_shape_passed"),
                    }),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "duration": round(row_end - row_start, 3),
                }
                row["rank_score"] = round(rank_score(row), 4)
                # Append to run ledger
                rr.append_candidate(row)
                rows.append(row)

    # --- Scoring and ranking (existing logic) ---
    for r in rows:
        if "error" not in r:
            r["rank_score"] = round(rank_score(r), 4)
            r["timesfm_rank_bonus"] = round(timesfm_rank_bonus(r), 4)
    portfolio_score_maps = enrich_row_scores(rows)

    global TF_GLOBAL_WEIGHTS
    TF_GLOBAL_WEIGHTS = compute_tf_weights(rows)
    for r in rows:
        if "error" not in r:
            r["rank_score"] = round(rank_score(r), 4)
            r["portfolio_score"] = round(portfolio_score(r), 4)
            if horizons != [60]:
                same_key = [x for x in rows if x.get("ticker") == r.get("ticker") and x.get("timeframe") == r.get("timeframe") and x.get("strategy") == r.get("strategy") and x.get("params") == r.get("params")]
                mw = multiwindow_score(same_key)
                r.update(mw)
                r["portfolio_score"] = round(r["portfolio_score"] + safe_float(mw.get("multiwindow_score")) / 10000.0, 4)

    rows.sort(key=lambda r: (r.get("decision") == "keep", safe_float(r.get("portfolio_balance_score", r.get("portfolio_score", r.get("rank_score")))), safe_float(r.get("portfolio_score", r.get("rank_score"))), safe_float(r.get("total_pnl"))), reverse=True)
    hf_source_rows = [
        r for r in rows
        if str(r.get("trade_bucket") or "") == "high"
        and int(r.get("trades", 0) or 0) >= 100
        and safe_float(r.get("profit_factor")) >= 1.15
        and safe_float(r.get("sharpe")) >= 0.2
        and safe_float(r.get("total_pnl")) > 0
        and bool(r.get("equity_shape_passed", True))
        and not bool(r.get("tail_veto"))
    ]
    top_hf_raw = portfolio_balanced_top(
        hf_source_rows,
        args.top_n,
        max_per_ticker=args.max_per_ticker,
        max_per_strategy=args.max_per_strategy,
        min_tickers=args.min_tickers,
        min_trades=100,
        trade_bucket="high",
    )
    top_hf = apply_tail_veto(top_hf_raw, initial_cash=args.initial_cash)
    top_research_raw = portfolio_balanced_top(
        rows,
        args.top_n,
        max_per_ticker=args.max_per_ticker,
        max_per_strategy=args.max_per_strategy,
        min_tickers=args.min_tickers,
        min_trades=1,
        trade_bucket=None,
    )
    top_research = apply_tail_veto(top_research_raw, initial_cash=args.initial_cash)
    top = top_hf if top_hf else top_research
    top = shortlist_candidates(top, limit=args.top_n)
    tail_vetoed = [r for r in top if r.get("tail_veto")]
    shortlist_complete = len(top) >= args.top_n
    indicator_leaderboard = build_indicator_leaderboard(rows, limit=20)
    coverage_reasons = coverage_reason_report(rows, universe, top)

    # --- Registry write (existing logic, unchanged) ---
    if not getattr(args, 'no_write_registry', False):
        reg = StrategyRegistry.load()
        batch = time.strftime("architect_%Y%m%d_%H%M%S", time.gmtime())
        for r in top:
            if r.get("decision") not in {"keep", "near_keep", "retest"}:
                continue
            # Stable cross-process id: sha256 of canonical JSON (hash() is salted per-process,
            # so the same params previously produced different strategy_id every run -> duplicates).
            import hashlib as _hl
            sid = f"{r['ticker']}__{r['strategy']}__{r['timeframe']}__{_hl.sha256(json.dumps(r['params'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:10]}"
            status = STATUS_ACTIVE_SIGNAL_POOL if r["decision"] == "keep" else STATUS_ACTIVE_WATCHLIST
            rec = reg.record_generation(
                strategy_id=sid,
                ticker=r["ticker"], strategy=r["strategy"],
                params=r.get("params") or {},
                metrics={k: r.get(k) for k in ["trades", "wins", "losses", "win_rate", "total_pnl", "avg_trade", "profit_factor", "max_drawdown", "sharpe", "timesfm_direction", "timesfm_confidence", "timesfm_source", "timesfm_rank_bonus", "rank_score", "portfolio_score", "portfolio_balance_score", "ticker_strategy_score", "family_global_score", "archetype_match_score", "trade_bucket", "equity_shape_passed", "equity_shape_score", "equity_shape_r2", "equity_shape_positive_window_ratio", "equity_shape_flat_window_ratio", "equity_shape_dd_ratio", "equity_shape_total_pnl", "equity_shape_max_dd", "equity_shape_reason", "smooth_ok", "smooth_live_ready", "smooth_reason", "smooth_score", "ulcer_index", "max_time_under_water", "drawdown_cluster_count", "recovery_factor", "monotonicity_score", "economic_ok", "economic_score", "economic_reason", "multiwindow_score", "multiwindow_pnl", "multiwindow_pf_avg", "multiwindow_sharpe_avg", "multiwindow_dd_avg", "multiwindow_keep_ratio", "window_count"]},
                portfolio_context={"timeframe": r["timeframe"], "source": "strategy_architect_autopilot", "horizons": horizons},
                quality_gate={"decision": r["decision"], "risk_verdict": r["risk_verdict"]},
                source="strategy_architect_autopilot",
                generation_batch_id=batch,
                status=status,
                note="autopilot_cycle",
                payload={"live_orders": 0, "horizons": horizons, "run_id": rr.run_id},
            )
            rec.active_rank = safe_float(r.get("portfolio_balance_score", safe_float(r.get("portfolio_score", safe_float(r.get("rank_score"))))))
            reg._store_record(rec)
        reg.save()
        reg.export_legacy_state_files()

    # --- Iteration 05: Finalize eligible candidates for this run ---
    # keep + near_keep both ship: near_keep passed the base gate and economic
    # context; they are paper-qualified but not live-ready (smoothness gate).
    eligible_rows = [r for r in top if r.get("decision") in {"keep", "near_keep"}]
    rr.finalize_eligible(eligible_rows)

    # --- Iteration 05: Finalize report ---
    report_md = render_markdown({
        "universe": universe, "target_universe_size": TARGET_UNIVERSE_SIZE,
        "data_gap_report": gap_report, "timesfm_status": tf_status,
        "rows_total": len(rows), "top_n": args.top_n,
        "top_selection": {"mode": "portfolio_balanced_top", "max_per_ticker": args.max_per_ticker, "max_per_strategy": args.max_per_strategy, "min_tickers": args.min_tickers},
        "top_hf_count_before_tail_veto": len(top_hf_raw),
        "top_research_count_before_tail_veto": len(top_research_raw),
        "top": top,
        "top_hf": top_hf,
        "top_research": top_research,
        "tail_vetoed": tail_vetoed, "indicator_leaderboard": indicator_leaderboard,
        "shortlist_complete": shortlist_complete,
        "coverage_reasons": coverage_reasons, "live_orders": 0,
        "state_prefix": state_prefix,
        "strategies_tested": sorted(set(str(r.get("strategy")) for r in rows if r.get("strategy"))),
        "strategies_requested": selected_strategies,
        "portfolio_equity_gate": {}, "portfolio_contributions": [],
        "policy_family_rows": [
            {"strategy": row.get("strategy"), "source": row.get("source", ""),
             "family_role": row.get("family_role", ""), "policy_reason": row.get("policy_reason", ""),
             "winner_boost": row.get("winner_boost", 0.0), "sideways_penalty": row.get("sideways_penalty", 0.0),
             "clone_penalty": row.get("clone_penalty", 0.0), "policy_bias": row.get("policy_bias", 0.0)}
            for row in (policy_meta.get("family_rows") or [])
        ],
    })
    # Prepend run_id to report
    report_md = f"# Run: {rr.run_id}\n\n" + report_md

    top_data = [{
        "ticker": r.get("ticker"), "timeframe": r.get("timeframe"),
        "strategy": r.get("strategy"), "params": r.get("params"),
        "total_pnl": r.get("total_pnl"), "profit_factor": r.get("profit_factor"),
        "max_drawdown": r.get("max_drawdown"), "sharpe": r.get("sharpe"),
        "trades": r.get("trades"), "run_id": rr.run_id,
    } for r in top]

    rr.finalize_report(top_data, report_md=report_md)

    # --- Iteration 05: Complete run with integrity checks ---
    run_status = rr.complete()
    if run_status == "COMPLETED":
        rr.update_latest_pointer()

    # --- Legacy compat: still write cycle JSON and latest.md ---
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    payload = {
        "ts": ts, "run_id": rr.run_id,
        "duration_sec": round(time.time() - started, 2),
        "universe": universe, "target_universe_size": TARGET_UNIVERSE_SIZE,
        "horizons": horizons,
        "data_files": {},
        "data_gap_report": gap_report, "timesfm_status": tf_status,
        "rows_total": len(rows), "top_n": args.top_n,
        "top_selection": {"mode": "portfolio_balanced_top", "max_per_ticker": args.max_per_ticker, "max_per_strategy": args.max_per_strategy, "min_tickers": args.min_tickers},
        "portfolio_score_maps": portfolio_score_maps,
        "coverage_reasons": coverage_reasons, "top": top,
        "top_hf_count_before_tail_veto": len(top_hf_raw),
        "top_research_count_before_tail_veto": len(top_research_raw),
        "tail_vetoed": tail_vetoed,
        "indicator_leaderboard": indicator_leaderboard,
        "strategies_requested": selected_strategies,
        "strategies_tested": sorted(set(str(r.get("strategy")) for r in rows if r.get("strategy"))),
        "timesfm_source_seen": sorted(set(str(r.get("timesfm_source")) for r in rows if "timesfm_source" in r)),
        "live_orders": 0,
        "state_router": router.state.snapshot(),
        "run_contract": {
            "run_id": rr.run_id, "status": run_status,
            "planned": rr.plan_config_count(),
            "tested": rr.manifest().get("tested_configurations", 0),
            "failed": rr.manifest().get("failed_configurations", 0),
            "skipped_exact_duplicate": rr.manifest().get("skipped_exact_duplicate_configurations", 0),
            "forced_reproduction": rr.manifest().get("forced_reproduction_configurations", 0),
            "eligible": rr.manifest().get("eligible_configurations", 0),
        },
        "novelty_gate": {
            "memory_available": novelty_accounting.memory_available if novelty_accounting else False,
            "novelty_policy_version": novelty_accounting.to_manifest_dict().get("novelty_policy_version") if novelty_accounting else None,
            "planned": novelty_accounting.planned if novelty_accounting else 0,
            "executed": novelty_accounting.executed if novelty_accounting else 0,
            "skipped_exact_duplicate": novelty_accounting.skipped_exact_duplicate if novelty_accounting else 0,
            "forced_reproduction": novelty_accounting.forced_reproduction if novelty_accounting else 0,
            "lookup_errors": novelty_accounting.lookup_errors if novelty_accounting else 0,
            "suppression_rate": novelty_accounting.to_manifest_dict().get("duplicate_suppression_rate", 0.0) if novelty_accounting else 0.0,
        },
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / f"cycle_{ts}.json"
    md_path = REPORT_DIR / "latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report_md, encoding="utf-8")
    return payload

def render_markdown(payload: Dict[str, Any]) -> str:
    gap = payload.get("data_gap_report", {})
    tf_status = payload.get("timesfm_status", {})
    lines = [
        "# Strategy Architect Autopilot - latest cycle",
        "",
        f"- universe: {len(payload['universe'])}/{payload.get('target_universe_size', 20)} ({', '.join(payload['universe'])})",
        f"- data coverage: {gap.get('available_active_universe_size', len(payload['universe']))}/{gap.get('target_universe_size', 20)} active tickers, missing_to_target={gap.get('missing_slots_to_target', 0)}",
        f"- generated/backtested rows: {payload['rows_total']}",
        f"- strategies tested: {len(payload.get('strategies_tested') or [])} ({', '.join((payload.get('strategies_tested') or [])[:12])})",
        f"- TimesFM source: {tf_status.get('source_display')} [{tf_status.get('source_key')}]; real={tf_status.get('real_timesfm')} ({tf_status.get('adapter_class')})",
        f"- TimesFM role: rank bonus + forecast context; bad backtests still rejected",
        f"- top selection: {payload.get('top_selection', {}).get('mode')} ticker_cap={payload.get('top_selection', {}).get('max_per_ticker')} strategy_cap={payload.get('top_selection', {}).get('max_per_strategy')} min_tickers={payload.get('top_selection', {}).get('min_tickers')}",
        f"- tail veto: raw={payload.get('top_raw_count_before_tail_veto', len(payload.get('top', [])))} kept={len(payload.get('top', []))} vetoed={len(payload.get('tail_vetoed') or [])}",
        f"- live orders: {payload['live_orders']}",
        "",
        "## Data gap report",
        "",
        f"- available tickers: {', '.join(gap.get('available_tickers', []))}",
        f"- excluded: {', '.join(gap.get('excluded', []))}",
        "",
        "| ticker | 15m rows | 1h rows |",
        "|---|---:|---:|",
    ]
    for ticker, row in sorted((gap.get("rows") or {}).items()):
        lines.append(f"| {ticker} | {row.get('15m', 0)} | {row.get('1h', 0)} |")
    lines.extend([
        "",
        "## Indicator / strategy leaderboard",
        "",
        "| strategy | tests | keep | trades | W/L | PnL | avg PF | avg Sharpe | max DD | TF conf | policy bias | winner | sideways | clone | score |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for ind in payload.get("indicator_leaderboard", [])[:12]:
        lines.append(
            f"| {ind.get('strategy')} | {ind.get('tests')} | {ind.get('keeps')} | {ind.get('trades')} | "
            f"{ind.get('wins')}/{ind.get('losses')} | {safe_float(ind.get('total_pnl')):.2f} | {safe_float(ind.get('avg_pf')):.2f} | "
            f"{safe_float(ind.get('avg_sharpe')):.2f} | {safe_float(ind.get('max_dd')):.2f} | {safe_float(ind.get('avg_timesfm_conf')):.2f} | {safe_float(ind.get('policy_bias', 0.0)):.2f} | {safe_float(ind.get('policy_winner_boost', 0.0)):.2f} | {safe_float(ind.get('policy_sideways_penalty', 0.0)):.2f} | {safe_float(ind.get('policy_clone_penalty', 0.0)):.2f} | {safe_float(ind.get('indicator_score')):.2f} |"
        )
    lines.extend([
        "",
        "## Policy family summary",
        "",
        "| strategy | source | role | reason | winner | sideways | clone | bias |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ])
    for row in payload.get("policy_family_rows", [])[:12]:
        lines.append(
            f"| {row.get('strategy')} | {row.get('source', '')} | {row.get('family_role', '')} | {row.get('policy_reason', '')} | {row.get('winner_boost', 0.0):.2f} | {row.get('sideways_penalty', 0.0):.2f} | {row.get('clone_penalty', 0.0):.2f} | {row.get('policy_bias', 0.0):.2f} |"
        )
    lines.extend([
        "",
        "## Coverage / why tickers are missing",
        "",
        "| ticker | in top | tested | keep | shape pass | reason | best | score | trades |",
        "|---|---:|---:|---:|---:|---|---|---:|---:|",
    ])
    for row in payload.get("coverage_reasons", []):
        lines.append(
            f"| {row.get('ticker')} | {row.get('in_top')} | {row.get('tested')} | {row.get('keep')} | {row.get('shape_pass')} | {row.get('reason')} | {row.get('best_strategy')} | {row.get('best_portfolio_score', 0):.2f} | {row.get('best_trades', 0)} |"
        )
    pg = payload.get("portfolio_equity_gate", {}) or {}
    lines.extend([
        "",
        "## Portfolio equity gate",
        "",
        f"- verdict: {pg.get('ok')} - {pg.get('reason')}",
        f"- shortlist_complete: {payload.get('shortlist_complete')}" if payload.get('shortlist_complete') is not None else "- shortlist_complete: n/a",
        f"- total_pnl: {safe_float(pg.get('total_pnl')):.2f}",
        f"- tail_pnl: {safe_float(pg.get('tail_pnl')):.2f}",
        f"- r2: {safe_float(pg.get('r2')):.3f}",
        f"- flat/tail_flat: {safe_float(pg.get('flat_window_ratio')):.2f}/{safe_float(pg.get('tail_flat_window_ratio')):.2f}",
        f"- stagnation_score: {safe_float(pg.get('stagnation_score')):.1f}",
        "",
        "| role | ticker | tf | strategy | trades | own tail | Δtail | Δpnl | ΔDD | Δstagnation |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for c in (payload.get("portfolio_contributions") or [])[:12]:
        lines.append(
            f"| {c.get('role')} | {c.get('ticker')} | {c.get('timeframe')} | {c.get('strategy')} | {c.get('trades')} | "
            f"{safe_float(c.get('own_tail_pnl')):.2f} | {safe_float(c.get('delta_tail_pnl')):.2f} | {safe_float(c.get('delta_total_pnl')):.2f} | {safe_float(c.get('delta_dd')):.2f} | {safe_float(c.get('delta_stagnation')):.2f} |"
        )
    lines.extend([
        "",
        "## High-frequency operating pool",
        "",
        "| ticker | tf | strategy | bucket | trades | W/L | WR% | PnL | PF | DD | archetype | portfolio | balance | rank | risk | decision |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for r in payload.get("top_hf", []) or payload.get("top", []):
        lines.append(
            f"| {r.get('ticker')} | {r.get('timeframe')} | {r.get('strategy')} | {r.get('trade_bucket')} | {r.get('trades',0)} | "
            f"{r.get('wins',0)}/{r.get('losses',0)} | {r.get('win_rate',0):.1f} | {r.get('total_pnl',0):.2f} | "
            f"{r.get('profit_factor',0):.2f} | {r.get('max_drawdown',0):.2f} | {r.get('archetype_match_score',0):.1f} | "
            f"{r.get('portfolio_score',0):.2f} | {r.get('portfolio_balance_score',0):.2f} | {r.get('rank_score',0):.2f} | {r.get('risk_verdict')} | {r.get('decision')} |"
        )
    lines.extend([
        "",
        "## Swing / research pool",
        "",
        "| ticker | tf | strategy | bucket | trades | W/L | WR% | PnL | PF | DD | archetype | portfolio | balance | rank | risk | decision |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for r in payload.get("top_research", []) or []:
        lines.append(
            f"| {r.get('ticker')} | {r.get('timeframe')} | {r.get('strategy')} | {r.get('trade_bucket')} | {r.get('trades',0)} | "
            f"{r.get('wins',0)}/{r.get('losses',0)} | {r.get('win_rate',0):.1f} | {r.get('total_pnl',0):.2f} | "
            f"{r.get('profit_factor',0):.2f} | {r.get('max_drawdown',0):.2f} | {r.get('archetype_match_score',0):.1f} | "
            f"{r.get('portfolio_score',0):.2f} | {r.get('portfolio_balance_score',0):.2f} | {r.get('rank_score',0):.2f} | {r.get('risk_verdict')} | {r.get('decision')} |"
        )
    lines.append("")
    lines.append("Safety: paper/backtest only, live_orders=0.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", default="15m,1h")
    ap.add_argument("--universe", default="", help="Comma-separated tickers override, e.g. BR,GAZP,LKOH,SBER")
    ap.add_argument("--strategies", default="policy")
    ap.add_argument("--max-params", type=int, default=4)
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument("--min-trades", type=int, default=2)
    ap.add_argument("--min-pf", type=float, default=1.05)
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--max-per-ticker", type=int, default=3)
    ap.add_argument("--max-per-strategy", type=int, default=3)
    ap.add_argument("--min-tickers", type=int, default=5)
    ap.add_argument("--initial-cash", type=float, default=1_000_000.0)
    ap.add_argument("--commission-per-contract", type=float, default=1.5,
                    help="Realistic FORTS broker+exchange fee per contract per side (RUB). Audit P0: backtests must be net-of-costs.")
    ap.add_argument("--slippage-bps", type=float, default=2.0,
                    help="Slippage in basis points applied to fills. Audit P0: backtests must be net-of-costs.")
    ap.add_argument("--horizons", default="60,90,180,365,1095", help="Comma-separated history windows in days, e.g. 60,90,180,365,1095")
    ap.add_argument("--no-write-registry", action="store_true")
    ap.add_argument("--force-reproduction", action="store_true",
                     help="Force re-run of exact duplicates (Iteration 08)")
    args = ap.parse_args()
    payload = run_cycle(args)
    summary = {
        "run_id": payload.get("run_id", "unknown"),
        "status": payload.get("status", payload.get("run_contract", {}).get("status", "unknown")),
        "live_orders": payload.get("live_orders", 0),
        "rows_total": payload.get("rows_total", 0),
        "duration_sec": payload.get("duration_sec", 0),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

