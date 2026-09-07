#!/usr/bin/env python3
"""Indicator rotation policy for Strategy Architect Autopilot.

Builds the next cycle's indicator/strategy family list from:
- previous indicator leaderboard,
- under-tested strategy families from futures_lab strategy zoo,
- TimesFM regime context from last cycle,
- mandatory exploration quota.

No broker calls, no live orders.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROP_ROOT = PROJECT_ROOT.parent
FUTURES_LAB = PROP_ROOT / "futures_lab"
REPORT_DIR = PROJECT_ROOT / "reports" / "strategy_architect"
POLICY_PATH = REPORT_DIR / "indicator_rotation_policy.json"
POLICY_MD = REPORT_DIR / "indicator_rotation_policy.md"
LATEST_CYCLE = REPORT_DIR / "latest.md"
EQUITY_SHAPE_REPORT = REPORT_DIR / "equity_shape_filter_latest.json"
EQUITY_DIVERSITY_REPORT = REPORT_DIR / "equity_diversity_filter_latest.json"

sys.path.insert(0, str(FUTURES_LAB))
from futures_lab import ZOO_PARAM_GRIDS  # type: ignore  # noqa:E402

DEFAULT_BASE = [
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

TREND_FAMILIES = [
    "sma_cross", "macd_trend", "atr_breakout", "ft_adx_sma", "ft_supertrend",
    "peet_adx_momentum", "dual_ma_adx_filter", "supertrend_ema_combo",
    "donchian_breakout", "opening_range_breakout", "intraday_momentum",
]

FLAT_FAMILIES = [
    "bollinger_reversion", "rsi_reversal", "vwap_reversion", "keltner_reversion",
    "ft_bband_rsi", "ft_multi_rsi", "vwap_bands", "stochastic_cross",
    "rsi_extreme_fade_volume", "mfi_divergence", "nateemma_basket_meanrev",
]

VOL_FAMILIES = [
    "volatility_squeeze", "atr_breakout", "atr_trailing_stop", "keltner_reversion",
    "inside_bar_breakout", "pinbar_reversal", "cci_channel_breakout",
]


def latest_cycle_json() -> Dict[str, Any]:
    files = sorted(REPORT_DIR.glob("cycle_*.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {}


def all_families() -> List[str]:
    return sorted(str(k) for k in ZOO_PARAM_GRIDS.keys())


def prior_usage(cycle: Dict[str, Any]) -> Dict[str, int]:
    out = {name: 0 for name in all_families()}
    for row in cycle.get("indicator_leaderboard", []) or []:
        out[str(row.get("strategy"))] = int(row.get("tests", 0) or 0)
    return out


def equity_shape_family_scores() -> Dict[str, float]:
    """Score families by SBER-like rising equity from latest shape audit."""
    if not EQUITY_SHAPE_REPORT.exists():
        return {}
    try:
        payload = json.loads(EQUITY_SHAPE_REPORT.read_text(encoding="utf-8"))
    except Exception:
        return {}
    scores: Dict[str, list[float]] = {}
    for row in payload.get("rows", []) or []:
        if row.get("equity_shape_passed") is not True:
            continue
        fam = str(row.get("strategy") or "")
        # SBER-like target: high R², positive windows, low flatness, low DD ratio.
        score = (
            float(row.get("equity_shape_score") or 0.0)
            + float(row.get("equity_shape_r2") or 0.0) * 2500.0
            + float(row.get("equity_shape_positive_window_ratio") or 0.0) * 2000.0
            - float(row.get("equity_shape_flat_window_ratio") or 0.0) * 2000.0
            - float(row.get("equity_shape_dd_ratio") or 0.0) * 1000.0
        )
        if fam:
            scores.setdefault(fam, []).append(score)
    return {fam: sum(vals) / max(1, len(vals)) for fam, vals in scores.items()}


def equity_diversity_family_scores() -> Dict[str, Dict[str, float]]:
    """Return family-level boosts and penalties from latest diversity audit."""
    if not EQUITY_DIVERSITY_REPORT.exists():
        return {}
    try:
        payload = json.loads(EQUITY_DIVERSITY_REPORT.read_text(encoding="utf-8"))
    except Exception:
        return {}
    summary = payload.get("family_summary", []) or []
    out: Dict[str, Dict[str, float]] = {}
    for row in summary:
        fam = str(row.get("strategy") or "")
        if not fam:
            continue
        out[fam] = {
            "winner_boost": float(row.get("winner_boost") or 0.0),
            "sideways_penalty": float(row.get("sideways_penalty") or 0.0),
            "clone_penalty": float(row.get("clone_penalty") or 0.0),
            "policy_bias": float(row.get("policy_bias") or 0.0),
        }
    return out


def regime_hint(cycle: Dict[str, Any]) -> str:
    dirs = []
    confs = []
    for row in cycle.get("top", []) or []:
        d = str(row.get("timesfm_direction", "flat"))
        c = float(row.get("timesfm_confidence", 0) or 0)
        dirs.append(d)
        confs.append(c)
    avg_conf = sum(confs) / max(1, len(confs))
    non_flat = [d for d in dirs if d in {"up", "down"}]
    if avg_conf < 0.35:
        return "uncertain"
    if len(non_flat) >= max(2, len(dirs) // 3):
        return "trend"
    return "flat"


def select_policy(target: int = 14, explore: int = 3) -> Dict[str, Any]:
    cycle = latest_cycle_json()
    leaderboard = cycle.get("indicator_leaderboard", []) or []
    regime = regime_hint(cycle)
    used = prior_usage(cycle)
    shape_scores = equity_shape_family_scores()
    diversity_scores = equity_diversity_family_scores()

    selected: List[str] = []
    reasons: Dict[str, str] = {}
    family_rows: List[Dict[str, Any]] = []

    def family_policy_row(name: str) -> Dict[str, Any]:
        diversity = diversity_scores.get(name, {})
        shape_score = float(shape_scores.get(name, 0.0))
        winner_boost = float(diversity.get("winner_boost", 0.0))
        sideways_penalty = float(diversity.get("sideways_penalty", 0.0))
        clone_penalty = float(diversity.get("clone_penalty", 0.0))
        policy_bias = float(diversity.get("policy_bias", 0.0))
        adjusted_bias = round(shape_score + winner_boost - sideways_penalty - clone_penalty, 4)
        if name not in diversity_scores and shape_score > 0:
            policy_reason = "equity_shape_sber_like"
        elif winner_boost > 0 and policy_bias >= 0:
            policy_reason = "winner_boost"
        elif clone_penalty > 0:
            policy_reason = "clone_penalty"
        elif sideways_penalty > 0:
            policy_reason = "sideways_penalty"
        else:
            policy_reason = "explore_keep"
        if winner_boost > 0 and policy_bias >= 0:
            family_role = "winner"
        elif clone_penalty > 0:
            family_role = "clone"
        elif sideways_penalty > 0:
            family_role = "sideways"
        else:
            family_role = "explore"
        return {
            "strategy": name,
            "shape_score": round(shape_score, 4),
            "winner_boost": round(winner_boost, 4),
            "sideways_penalty": round(sideways_penalty, 4),
            "clone_penalty": round(clone_penalty, 4),
            "policy_bias": adjusted_bias,
            "family_role": family_role,
            "policy_reason": policy_reason,
            "source": "equity_diversity" if name in diversity_scores else ("equity_shape" if shape_score > 0 else "fallback"),
        }

    # 1) first seed strong families from shape + diversity audit
    primary_rows = []
    for name in sorted(set(shape_scores) | set(diversity_scores)):
        if name in ZOO_PARAM_GRIDS:
            primary_rows.append(family_policy_row(name))
    primary_rows.sort(key=lambda row: (row["policy_bias"], row["winner_boost"], row["shape_score"], row["strategy"]), reverse=True)
    for row in primary_rows[:6]:
        name = row["strategy"]
        if name not in selected:
            selected.append(name)
            reasons[name] = row["policy_reason"]
        family_rows.append(row)

    # 2) keep proven families from leaderboard, but annotate them with diversity bias when present
    for row in leaderboard[:6]:
        name = str(row.get("strategy"))
        if name not in ZOO_PARAM_GRIDS or name in selected:
            continue
        policy_row = family_policy_row(name)
        policy_row.update({"leaderboard_score": float(row.get("indicator_score") or 0.0), "policy_reason": "leaderboard_top" if policy_row["policy_reason"] == "explore_keep" else policy_row["policy_reason"]})
        selected.append(name)
        reasons[name] = policy_row["policy_reason"]
        family_rows.append(policy_row)

    # 3) regime-conditioned families
    if regime == "trend":
        regime_pool = TREND_FAMILIES
    elif regime == "flat":
        regime_pool = FLAT_FAMILIES
    else:
        regime_pool = VOL_FAMILIES + TREND_FAMILIES[:3] + FLAT_FAMILIES[:3]
    for name in regime_pool:
        if name in ZOO_PARAM_GRIDS and name not in selected:
            row = family_policy_row(name)
            row["policy_reason"] = f"timesfm_regime_{regime}"
            row["source"] = "timesfm"
            family_rows.append(row)
            selected.append(name)
            reasons[name] = row["policy_reason"]
        if len(selected) >= target - explore:
            break

    # 4) mandatory exploration: least-tested families from all zoo
    least_tested = sorted(all_families(), key=lambda n: (used.get(n, 0), n))
    added_explore = 0
    for name in least_tested:
        if name not in selected:
            row = family_policy_row(name)
            row["policy_reason"] = "mandatory_exploration_least_tested"
            family_rows.append(row)
            selected.append(name)
            reasons[name] = row["policy_reason"]
            added_explore += 1
        if added_explore >= explore or len(selected) >= target:
            break

    # 5) fallback defaults
    for name in DEFAULT_BASE:
        if len(selected) >= target:
            break
        if name in ZOO_PARAM_GRIDS and name not in selected:
            row = family_policy_row(name)
            row["policy_reason"] = "fallback_default"
            family_rows.append(row)
            selected.append(name)
            reasons[name] = row["policy_reason"]

    family_rows.sort(key=lambda row: (row["policy_bias"], row["winner_boost"], row["shape_score"], row["strategy"]), reverse=True)
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target": target,
        "explore": explore,
        "timesfm_regime_hint": regime,
        "selected": selected[:target],
        "reasons": {k: reasons.get(k, "") for k in selected[:target]},
        "equity_shape_family_scores": shape_scores,
        "equity_diversity_family_scores": diversity_scores,
        "family_rows": family_rows[:target],
        "all_families": all_families(),
        "live_orders": 0,
    }
    return payload


def render_md(payload: Dict[str, Any]) -> str:
    lines = [
        "# Indicator Rotation Policy — latest",
        "",
        f"- TimesFM regime hint: {payload['timesfm_regime_hint']}",
        f"- selected families: {len(payload['selected'])}/{payload['target']}",
        f"- exploration slots: {payload['explore']}",
        f"- live_orders: {payload['live_orders']}",
        "",
        "## Policy rows",
        "",
        "| # | indicator family | source | role | policy reason | winner boost | sideways penalty | clone penalty | bias |",
        "|---:|---|---|---|---|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(payload.get("family_rows", []), 1):
        lines.append(
            f"| {i} | {row.get('strategy')} | {row.get('source', '')} | {row.get('family_role', '')} | {row.get('policy_reason', '')} | {row.get('winner_boost', 0.0):.2f} | {row.get('sideways_penalty', 0.0):.2f} | {row.get('clone_penalty', 0.0):.2f} | {row.get('policy_bias', 0.0):.2f} |"
        )
    lines.extend(["", "## Selected families", "", "| # | indicator family | reason |", "|---:|---|---|"])
    for i, name in enumerate(payload["selected"], 1):
        lines.append(f"| {i} | {name} | {payload['reasons'].get(name, '')} |")
    return "\n".join(lines)


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = select_policy()
    POLICY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    POLICY_MD.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps({"selected": payload["selected"], "regime": payload["timesfm_regime_hint"], "live_orders": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
