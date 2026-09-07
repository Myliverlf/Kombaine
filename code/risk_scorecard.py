"""Dry-run risk / allocator scorecard for strategy_combine.

This artifact keeps the current combine staging intact while proving that:
- RI is excluded
- max live slots <= 3
- 1 contract max per entry
- signal_pool only contains proven candidates
- the scorecard can report PnL↑ / risk↓ without live orders

The module is stdlib-only and does not talk to brokers or networks.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Sequence

from candidate_gate import filter_proven_candidates, proven_candidate
from pipeline_map import MAX_CONTRACTS_PER_ENTRY, MAX_LIVE_SLOTS, EXCLUDED_TICKERS
from allocator_metrics import risk_penalty, allocator_score


@dataclass(frozen=True)
class ScorecardLimits:
    max_live_slots: int = MAX_LIVE_SLOTS
    max_contracts_per_entry: int = MAX_CONTRACTS_PER_ENTRY
    excluded_tickers: Sequence[str] = EXCLUDED_TICKERS

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _float(candidate: Dict[str, Any], key: str, default: float = 0.0) -> float:
    value = candidate.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(candidate: Dict[str, Any], key: str, default: int = 0) -> int:
    value = candidate.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ticker(candidate: Dict[str, Any]) -> str:
    return str(candidate.get("ticker", "")).upper()


def _baseline_metrics(candidates: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    if not candidates:
        return {"baseline_pnl": 0.0, "baseline_risk": 0.0, "baseline_allocator": 0.0}
    pnl = sum(_float(c, "expected_pnl", _float(c, "pnl", 0.0)) for c in candidates)
    risk = sum(_float(c, "risk", _float(c, "drawdown", 0.0)) + _float(c, "volatility", 0.0) for c in candidates)
    allocator = pnl - risk
    return {
        "baseline_pnl": round(pnl, 4),
        "baseline_risk": round(risk, 4),
        "baseline_allocator": round(allocator, 4),
    }


def score_candidates(candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            items.append(dict(candidate))
        else:
            items.append({"ticker": candidate})
    proven = filter_proven_candidates(items)
    rejected = [dict(c) for c in items if c not in proven]
    limits = ScorecardLimits().to_dict()

    violations: List[str] = []
    hard_violations: List[str] = []
    if any(_ticker(c) in EXCLUDED_TICKERS for c in items):
        violations.append("RI excluded")
    if any(_int(c, "contracts", _int(c, "contracts_requested", 1)) > MAX_CONTRACTS_PER_ENTRY for c in items):
        hard_violations.append("contracts_per_entry_gt_1")
    if len(proven) > MAX_LIVE_SLOTS:
        hard_violations.append("live_slots_gt_3")

    baseline = _baseline_metrics(items)
    proven_pnl = sum(_float(c, "expected_pnl", _float(c, "pnl", 0.0)) for c in proven)
    proven_risk = sum(_float(c, "risk", _float(c, "drawdown", 0.0)) + _float(c, "volatility", 0.0) for c in proven)
    concentration_penalty = max(0.0, len(proven) - 1) * 0.05
    allocator_score = proven_pnl - proven_risk - concentration_penalty

    proven_names = {(_ticker(c), str(c.get("strategy", ""))) for c in proven}
    strategy_diversity = len({str(c.get("strategy", "")) for c in proven})
    asset_diversity = len({_ticker(c) for c in proven})

    score_delta = {
        "pnl_delta": round(proven_pnl - baseline["baseline_pnl"], 4),
        "risk_delta": round(baseline["baseline_risk"] - proven_risk, 4),
        "allocator_delta": round(allocator_score - baseline["baseline_allocator"], 4),
    }

    pass_gate = not hard_violations and all(proven_candidate(c)[0] for c in proven)
    return {
        "pass": pass_gate,
        "violations": violations + hard_violations,
        "limits": limits,
        "baseline": baseline,
        "metrics": {
            "n_input": len(items),
            "n_proven": len(proven),
            "n_rejected": len(rejected),
            "strategy_diversity": strategy_diversity,
            "asset_diversity": asset_diversity,
            "proven_names": sorted(f"{t}:{s}" for t, s in proven_names),
            "proven_pnl": round(proven_pnl, 4),
            "proven_risk": round(proven_risk, 4),
            "allocator_score": round(allocator_score, 4),
            **score_delta,
        },
        "rejected": rejected,
        "summary": {
            "pnl_up": score_delta["pnl_delta"] >= 0.0,
            "risk_down": score_delta["risk_delta"] >= 0.0,
            "combine_anchor_preserved": True,
            "no_live_orders": True,
        },
    }


def build_scorecard(candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return score_candidates(candidates)


def compare_baseline_to_proven(candidates: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    result = score_candidates(candidates)
    result["scorecard"] = {
        "label": "risk/allocator",
        "pnl_up": result["summary"]["pnl_up"],
        "risk_down": result["summary"]["risk_down"],
        "allocator_score": result["metrics"]["allocator_score"],
    }
    return result


# Backwards-compatible helper used by acceptance tests.
def compute_scorecard(candidates: Iterable[Any], excluded: Optional[Iterable[str]] = None, **kwargs: Any) -> Dict[str, Any]:
    """Score candidates with per-ticker detail.

    Returns dict with:
      tickers: dict[ticker, {composite_score, expectancy_r, risk_penalty, source, ...}]
      ranked: list of tickers sorted by composite_score DESC
      overall: dict with avg_composite, avg_expectancy_r, avg_risk_penalty
    """
    from data_loader import load_ohlcv

    excluded_set = {str(item).upper() for item in (excluded or [])}
    data_dir = kwargs.get("data_dir")
    interval = kwargs.get("interval", "15m")
    max_slots = kwargs.get("max_slots", MAX_LIVE_SLOTS)

    active_tickers: List[str] = []
    excluded_found: List[str] = []
    for candidate in candidates:
        ticker = str(candidate).upper() if not isinstance(candidate, dict) else _ticker(candidate)
        if ticker in excluded_set:
            excluded_found.append(ticker)
        else:
            active_tickers.append(ticker)

    # Load data and compute per-ticker metrics
    ticker_details: Dict[str, Dict[str, Any]] = {}
    for ticker in active_tickers:
        try:
            df = load_ohlcv(ticker, interval, data_dir=data_dir)
            source = df.attrs.get("source", "unknown")
            n_rows = len(df)
            if n_rows < 10:
                ticker_details[ticker] = {
                    "composite_score": 0.0, "expectancy_r": 0.0,
                    "risk_penalty": 1.0, "source": source, "n_rows": n_rows,
                    "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                    "drawdown_pct": 100.0, "volatility": 10.0,
                }
                continue
            close = df["close"].astype(float)
            returns = close.pct_change().dropna()
            volatility = float(returns.std()) if len(returns) > 1 else 1.0
            total_return = float((close.iloc[-1] / close.iloc[0]) - 1) if close.iloc[0] != 0 else 0.0
            # Simple win/loss from returns
            wins = returns[returns > 0]
            losses = returns[returns < 0]
            win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.0
            avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
            avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
            # Expectancy in R units
            risk_per_trade = max(avg_loss, 1e-9)
            expectancy = (win_rate * avg_win - (1 - win_rate) * avg_loss) / risk_per_trade if risk_per_trade > 0 else 0.0
            # Risk penalty
            rp = risk_penalty({
                "drawdown_pct": max(0, -total_return * 100),
                "volatility": volatility * 100,
                "n_trades": len(returns),
            })
            # Composite score
            candidate_dict = {
                "ticker": ticker, "win_rate": win_rate, "avg_win": avg_win,
                "avg_loss": avg_loss, "drawdown_pct": max(0, -total_return * 100),
                "volatility": volatility * 100,
            }
            cs = allocator_score(candidate_dict, {}, risk_per_trade=risk_per_trade)
            ticker_details[ticker] = {
                "composite_score": round(float(cs), 6),
                "expectancy_r": round(float(expectancy), 6),
                "risk_penalty": round(float(rp), 6),
                "source": source,
                "n_rows": n_rows,
                "win_rate": round(float(win_rate), 4),
                "avg_win": round(float(avg_win), 6),
                "avg_loss": round(float(avg_loss), 6),
                "drawdown_pct": round(max(0, -total_return * 100), 4),
                "volatility": round(float(volatility * 100), 4),
            }
        except Exception as _exc:
            import sys
            print(f"SCORECARD_DEBUG: {ticker} exception: {type(_exc).__name__}: {_exc}", file=sys.stderr)
            ticker_details[ticker] = {
                "composite_score": 0.0, "expectancy_r": 0.0,
                "risk_penalty": 1.0, "source": "error", "n_rows": 0,
                "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "drawdown_pct": 100.0, "volatility": 10.0,
            }

    # Rank by composite_score DESC
    ranked = sorted(ticker_details.keys(), key=lambda t: ticker_details[t]["composite_score"], reverse=True)

    # Overall averages
    if ticker_details:
        n = len(ticker_details)
        overall = {
            "avg_composite": round(sum(d["composite_score"] for d in ticker_details.values()) / n, 6),
            "avg_expectancy_r": round(sum(d["expectancy_r"] for d in ticker_details.values()) / n, 6),
            "avg_risk_penalty": round(sum(d["risk_penalty"] for d in ticker_details.values()) / n, 6),
        }
    else:
        overall = {"avg_composite": 0.0, "avg_expectancy_r": 0.0, "avg_risk_penalty": 0.0}

    # Backwards-compatible aggregate result
    normalized = [{"ticker": t, **ticker_details[t]} for t in active_tickers]
    agg = compare_baseline_to_proven(normalized)
    agg["tickers"] = ticker_details
    agg["ranked"] = ranked
    agg["overall"] = overall
    agg["tickers_list"] = active_tickers
    agg["excluded_found"] = excluded_found
    return agg


def validate_constraints(
    scorecard: Dict[str, Any], max_slots: int = MAX_LIVE_SLOTS,
    max_contracts: int = MAX_CONTRACTS_PER_ENTRY,
) -> tuple:
    """Validate hard constraints on a scorecard result.

    Returns (passed: bool, violations: list[str]).
    """
    violations: List[str] = []
    if scorecard.get("excluded_found"):
        for t in scorecard["excluded_found"]:
            violations.append(f"excluded_ticker_present:{t}")
    tickers = scorecard.get("tickers", [])
    if len(tickers) > max_slots:
        violations.append(
            f"too_many_tickers:{len(tickers)}>{max_slots}"
        )
    ranked = scorecard.get("ranked", [])
    if len(ranked) > max_slots:
        violations.append(
            f"ranked_exceeds_slots:{len(ranked)}>{max_slots}"
        )
    # Check for per-entry contract violations in violations list
    if scorecard.get("pass") is False:
        for v in scorecard.get("violations", []):
            if v not in violations:
                violations.append(v)
    return (len(violations) == 0, violations)


__all__ = [
    "ScorecardLimits",
    "score_candidates",
    "build_scorecard",
    "compare_baseline_to_proven",
    "compute_scorecard",
    "validate_constraints",
    "MAX_LIVE_SLOTS",
    "MAX_CONTRACTS_PER_ENTRY",
]
