"""Portfolio-aware replacement policy for strategy_combine.

This module scores strategies against the current portfolio state and decides
whether a new candidate should be promoted into the active watchlist or remain
only in the registry/candidate stream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

try:
    from .strategy_registry import (
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_ACTIVE_WATCHLIST,
        STATUS_CONFLICTED,
        STATUS_REJECTED,
        STATUS_ROTATED_OUT,
        StrategyRecord,
    )
except ImportError:  # pragma: no cover - support direct top-level imports from code/
    from strategy_registry import (  # type: ignore
        STATUS_ACTIVE_SIGNAL_POOL,
        STATUS_ACTIVE_WATCHLIST,
        STATUS_CONFLICTED,
        STATUS_REJECTED,
        STATUS_ROTATED_OUT,
        StrategyRecord,
    )


@dataclass
class PortfolioState:
    balance_rub: float
    go_budget_rub: float
    used_go_rub: float
    max_slots: int
    open_tickers: set[str] = field(default_factory=set)
    allowed_tickers: set[str] = field(default_factory=set)
    regime_bias: str = "neutral"
    risk_limit_pct: float = 0.0
    stale_signal_ids: set[str] = field(default_factory=set)
    no_free_slots: bool = False
    existing_watchlist_size: int = 0
    active_slots: int = 0
    signal_pool_size: int = 0

    @property
    def free_slots(self) -> int:
        return max(self.max_slots - self.active_slots, 0)


@dataclass
class ReplacementDecision:
    promoted: bool
    replacement_id: Optional[str] = None
    rejected_reason: str = ""
    candidate_score: float = 0.0
    worst_score: float = 0.0
    effective_threshold: float = 0.0
    quality_gate_passed: bool = False
    status: str = STATUS_REJECTED


def _metric(metrics: dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in metrics and metrics[name] is not None:
            value = metrics[name]
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return float(default)


def adaptive_threshold(portfolio: PortfolioState) -> float:
    """Adaptive baseline for promotion.

    Higher balance/available slots lower the required threshold a bit;
    low balance, a consumed GO budget, and opened tickers increase it.
    """
    utilization = 0.0 if portfolio.go_budget_rub <= 0 else min(portfolio.used_go_rub / portfolio.go_budget_rub, 1.5)
    slot_pressure = 0.0 if portfolio.max_slots <= 0 else min(portfolio.active_slots / portfolio.max_slots, 1.5)
    open_pressure = len(portfolio.open_tickers) / max(portfolio.max_slots, 1)
    regime_penalty = 0.0 if portfolio.regime_bias == "neutral" else (75.0 if portfolio.regime_bias == "meanrev" else 50.0)
    balance_bonus = min(portfolio.balance_rub / 1000.0, 100.0)
    return 100.0 + utilization * 180.0 + slot_pressure * 120.0 + open_pressure * 80.0 + regime_penalty - balance_bonus


def _is_record_like(item: Any) -> bool:
    """True for StrategyRecord objects even when loaded through core bridge.

    Live imports may create a class identity mismatch between
    ``strategy_registry.StrategyRecord`` and the object re-exported by
    ``core.strategy_registry``.  Use duck typing instead of exact isinstance.
    """
    return hasattr(item, "strategy_id") and hasattr(item, "metrics") and hasattr(item, "portfolio_context")


def portfolio_aware_score(candidate: StrategyRecord | dict[str, Any], portfolio: PortfolioState) -> float:
    """Score a strategy with portfolio-aware adjustments."""
    if _is_record_like(candidate):
        metrics = candidate.metrics
        context = candidate.portfolio_context
        status = candidate.status
        ticker = candidate.ticker
        strategy = candidate.strategy
    else:
        metrics = candidate.get("metrics", {})
        context = candidate.get("portfolio_context", {})
        status = candidate.get("status", STATUS_ACTIVE_WATCHLIST)
        ticker = candidate.get("ticker", "")
        strategy = candidate.get("strategy", "")

    pnl = _metric(metrics, "expected_pnl", "pnl", "total_pnl", default=0.0)
    fi_score = _metric(metrics, "fi_score", "f_score", default=0.0)
    sharpe = _metric(metrics, "sharpe", default=0.0)
    pf = _metric(metrics, "pf", "profit_factor", default=1.0)
    win_rate = _metric(metrics, "win_rate", "win_rate_pct", default=0.0)
    trades = _metric(metrics, "trades", default=0.0)
    drawdown = abs(_metric(metrics, "dd", "max_drawdown", default=0.0))
    rank = _metric(metrics, "rank_score", default=0.0)
    quality_gate = 1.0 if _quality_gate_ok(metrics, portfolio, context) else 0.0

    balance_factor = min(portfolio.balance_rub / 50_000.0, 3.0)
    go_utilization = 0.0 if portfolio.go_budget_rub <= 0 else min(portfolio.used_go_rub / portfolio.go_budget_rub, 1.0)
    slot_factor = max(0.5, (portfolio.max_slots - portfolio.active_slots + 1) / max(portfolio.max_slots, 1))
    open_penalty = 0.0
    if ticker in portfolio.open_tickers:
        open_penalty = 10_000.0
    if portfolio.allowed_tickers and ticker not in portfolio.allowed_tickers:
        open_penalty += 5_000.0
    stale_penalty = 1_000.0 if candidate_id(candidate) in portfolio.stale_signal_ids else 0.0
    regime_bonus = _regime_bonus(portfolio.regime_bias, strategy, ticker)

    score = (
        pnl * 0.55
        + fi_score * 450.0
        + sharpe * 280.0
        + pf * 180.0
        + win_rate * 4.0
        + rank * 0.10
        + quality_gate * 500.0
        + balance_factor * 90.0
        + slot_factor * 140.0
        + regime_bonus
        - drawdown * 0.20
        - go_utilization * 220.0
        - open_penalty
        - stale_penalty
    )

    if status == STATUS_ROTATED_OUT:
        score -= 200.0
    elif status == STATUS_CONFLICTED:
        score -= 120.0
    elif status == STATUS_REJECTED:
        score -= 500.0
    elif status == STATUS_ACTIVE_SIGNAL_POOL:
        score += 35.0
    return score


def candidate_id(candidate: StrategyRecord | dict[str, Any]) -> str:
    if _is_record_like(candidate):
        return candidate.strategy_id
    return str(candidate.get("strategy_id") or candidate.get("id") or f'{candidate.get("ticker", "")}_{candidate.get("strategy", "")}')


def _regime_bonus(regime_bias: str, strategy: str, ticker: str) -> float:
    strategy_l = strategy.lower()
    if regime_bias == "trend":
        if any(tag in strategy_l for tag in ("trend", "breakout", "momentum")):
            return 180.0
        if any(tag in strategy_l for tag in ("meanrev", "reversion")):
            return -110.0
    if regime_bias == "meanrev" and any(tag in strategy_l for tag in ("meanrev", "reversion", "vwap")):
        return 150.0
    if ticker in {"RI", "Si"}:
        return -40.0
    return 0.0


def _quality_gate_ok(metrics: dict[str, Any], portfolio: PortfolioState, context: dict[str, Any]) -> bool:
    """15m / 15m-like quality gate."""
    pnl = _metric(metrics, "expected_pnl", "pnl", "total_pnl", default=0.0)
    trades = _metric(metrics, "trades", default=0.0)
    pf = _metric(metrics, "pf", "profit_factor", default=1.0)
    fi_score = _metric(metrics, "fi_score", "f_score", default=0.0)
    sharpe = _metric(metrics, "sharpe", default=0.0)
    regime_ok = bool(context.get("regime_ok", True))
    stale_ok = bool(context.get("stale_ok", True))
    contract_risk_ok = bool(context.get("contract_risk_ok", True))
    return (
        regime_ok
        and stale_ok
        and contract_risk_ok
        and pnl > 0
        and trades >= 8
        and pf >= 1.05
        and (fi_score >= 0.0 or sharpe >= 0.2)
    )


def evaluate_candidate(
    candidate: StrategyRecord | dict[str, Any],
    watchlist: Iterable[StrategyRecord | dict[str, Any]],
    portfolio: PortfolioState,
) -> ReplacementDecision:
    """Decide whether to promote candidate into watchlist.

    Promotion is blocked by no_free_slots, ticker conflicts and quality gates.
    """
    metrics = candidate.metrics if _is_record_like(candidate) else candidate.get("metrics", {})
    ticker = candidate.ticker if _is_record_like(candidate) else candidate.get("ticker", "")
    status = candidate.status if _is_record_like(candidate) else candidate.get("status", STATUS_ACTIVE_SIGNAL_POOL)
    cand_score = portfolio_aware_score(candidate, portfolio)
    quality_gate_passed = _quality_gate_ok(metrics, portfolio, candidate.portfolio_context if _is_record_like(candidate) else candidate.get("portfolio_context", {}))
    threshold = adaptive_threshold(portfolio)

    if portfolio.no_free_slots or portfolio.free_slots <= 0:
        return ReplacementDecision(
            promoted=False,
            rejected_reason="no_free_slots",
            candidate_score=cand_score,
            effective_threshold=threshold,
            quality_gate_passed=quality_gate_passed,
            status=STATUS_CONFLICTED,
        )
    if ticker in portfolio.open_tickers:
        return ReplacementDecision(
            promoted=False,
            rejected_reason="ticker_already_open",
            candidate_score=cand_score,
            effective_threshold=threshold,
            quality_gate_passed=quality_gate_passed,
            status=STATUS_CONFLICTED,
        )
    if portfolio.allowed_tickers and ticker not in portfolio.allowed_tickers:
        return ReplacementDecision(
            promoted=False,
            rejected_reason="ticker_not_allowed",
            candidate_score=cand_score,
            effective_threshold=threshold,
            quality_gate_passed=quality_gate_passed,
            status=STATUS_REJECTED,
        )
    if not quality_gate_passed:
        return ReplacementDecision(
            promoted=False,
            rejected_reason="quality_gate_failed",
            candidate_score=cand_score,
            effective_threshold=threshold,
            quality_gate_passed=False,
            status=STATUS_REJECTED,
        )

    active_watchlist = [item for item in watchlist if _watchlist_status(item) == STATUS_ACTIVE_WATCHLIST]
    if not active_watchlist:
        return ReplacementDecision(
            promoted=True,
            candidate_score=cand_score,
            effective_threshold=threshold,
            quality_gate_passed=True,
            status=STATUS_ACTIVE_WATCHLIST,
        )

    worst = min(active_watchlist, key=lambda item: portfolio_aware_score(item, portfolio))
    worst_score = portfolio_aware_score(worst, portfolio)
    improvement_margin = max(40.0, threshold * 0.08)
    if cand_score <= worst_score + improvement_margin:
        return ReplacementDecision(
            promoted=False,
            replacement_id=strategy_identifier(worst),
            rejected_reason="below_replacement_bar",
            candidate_score=cand_score,
            worst_score=worst_score,
            effective_threshold=threshold,
            quality_gate_passed=True,
            status=STATUS_ACTIVE_SIGNAL_POOL,
        )
    return ReplacementDecision(
        promoted=True,
        replacement_id=strategy_identifier(worst),
        rejected_reason="replaced_worst_watchlist",
        candidate_score=cand_score,
        worst_score=worst_score,
        effective_threshold=threshold,
        quality_gate_passed=True,
        status=STATUS_ACTIVE_WATCHLIST,
    )


def strategy_identifier(item: StrategyRecord | dict[str, Any]) -> str:
    if _is_record_like(item):
        return item.strategy_id
    return str(item.get("strategy_id") or item.get("id") or f'{item.get("ticker", "")}_{item.get("strategy", "")}')


def _watchlist_status(item: StrategyRecord | dict[str, Any]) -> str:
    if _is_record_like(item):
        return item.status
    return str(item.get("status", STATUS_ACTIVE_WATCHLIST))


def classify_transition(decision: ReplacementDecision) -> str:
    if decision.promoted:
        return STATUS_ACTIVE_WATCHLIST
    return decision.status
