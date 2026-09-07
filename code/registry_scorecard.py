"""Registry Scorecard — composite scoring + ranking for enriched registry.

Pure module: dict-in → dict-out.  No broker, no I/O, no network.

Functions:
  - build_registry_scorecard(enriched_registry, config) → scored ranking
  - format_scorecard_report(scorecard) → markdown string

Dependencies:
  - code/allocator_metrics.py (allocator_score, WEIGHTS)
  - code/direction_enforcer.py (DirectionPolicy, VALID_DIRECTIONS)
"""
from typing import Any, Dict, List, Optional


def build_registry_scorecard(
    enriched_registry: Dict[str, Any],
    config: Dict[str, Any],
    regime_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a scorecard from an enriched registry.

    For each strategy:
      1. Skip if RI (config exclusion).
      2. Compute allocator_score via allocator_metrics.
      3. Apply max_slots and max_contracts constraints.
      4. Rank by allocator_score DESC.

    Args:
        enriched_registry: registry with direction enforced on each strategy.
        config: config dict with excluded[], risk.max_slots, risk.max_contracts_per_entry.
        regime_snapshot: optional regime snapshot for regime_bonus.

    Returns:
        {
            "ranked": [{"strategy_id", "direction", "pnl", "allocator_score",
                         "expectancy_r", "risk_penalty", "regime_bonus", "rank"}],
            "constraints": {"max_slots", "max_contracts_per_entry", "excluded"},
            "violations": [str, ...],
        }
    """
    from allocator_metrics import allocator_score, WEIGHTS
    from direction_enforcer import VALID_DIRECTIONS

    excluded_raw = config.get("excluded", [])
    excluded = {t.upper() for t in excluded_raw}
    risk_cfg = config.get("risk", {})
    max_slots = risk_cfg.get("max_slots", 3)
    max_contracts = risk_cfg.get("max_contracts_per_entry", 1)
    regime = regime_snapshot or {"tickers": {}, "bias": "neutral"}

    strategies = enriched_registry.get("strategies", {})
    ranked: List[Dict[str, Any]] = []
    violations: List[str] = []

    for sid, rec in strategies.items():
        ticker = rec.get("ticker", "")

        # RI exclusion
        if ticker.upper() in excluded:
            violations.append(f"{sid}: excluded ticker {ticker}")
            continue

        # Direction must be LONG or SHORT for scoring
        direction = rec.get("direction", "")
        if direction not in ("LONG", "SHORT"):
            violations.append(
                f"{sid}: direction={direction!r} — not scoreable (need LONG/SHORT)"
            )
            continue

        # Build candidate for allocator_score
        metrics = rec.get("metrics", {})
        candidate = {
            "ticker": ticker,
            "direction": direction,
            "win_rate": metrics.get("win_rate", 0.0),
            "avg_win": metrics.get("avg_win", 0.0),
            "avg_loss": metrics.get("avg_loss", 0.0),
            "drawdown_pct": metrics.get("drawdown_pct"),
            "volatility": metrics.get("volatility"),
        }

        score = allocator_score(candidate, regime)
        e_r = candidate["win_rate"] * candidate["avg_win"] - (
            1 - candidate["win_rate"]
        ) * candidate["avg_loss"]

        ranked.append({
            "strategy_id": sid,
            "ticker": ticker,
            "direction": direction,
            "pnl": metrics.get("pnl", 0.0),
            "allocator_score": score,
            "expectancy_r": round(e_r, 6),
            "trades": metrics.get("trades", 0),
        })

    # Sort by allocator_score DESC
    ranked.sort(key=lambda x: x["allocator_score"], reverse=True)

    # Apply max_slots limit
    if len(ranked) > max_slots:
        violations.append(
            f"truncated from {len(ranked)} to {max_slots} (max_slots limit)"
        )
        ranked = ranked[:max_slots]

    # Add rank
    for i, entry in enumerate(ranked):
        entry["rank"] = i + 1

    return {
        "ranked": ranked,
        "constraints": {
            "max_slots": max_slots,
            "max_contracts_per_entry": max_contracts,
            "excluded": sorted(excluded),
        },
        "violations": violations,
    }


def format_scorecard_report(scorecard: Dict[str, Any]) -> str:
    """Format scorecard as a markdown report.

    Args:
        scorecard: output of build_registry_scorecard.

    Returns:
        Markdown string with ranked table and violations.
    """
    ranked = scorecard.get("ranked", [])
    constraints = scorecard.get("constraints", {})
    violations = scorecard.get("violations", [])

    lines: List[str] = []
    lines.append("# Registry Scorecard Report")
    lines.append("")
    lines.append(
        f"Constraints: max_slots={constraints.get('max_slots', '?')}, "
        f"max_contracts_per_entry={constraints.get('max_contracts_per_entry', '?')}, "
        f"excluded={constraints.get('excluded', [])}"
    )
    lines.append("")

    if not ranked:
        lines.append("*No strategies scored.*")
    else:
        # Table header
        lines.append(
            "| Rank | Strategy | Ticker | Direction | PnL | Allocator Score | Trades |"
        )
        lines.append(
            "|------|----------|--------|-----------|-----|-----------------|--------|"
        )
        for entry in ranked:
            lines.append(
                f"| {entry['rank']} "
                f"| {entry['strategy_id']} "
                f"| {entry.get('ticker', '')} "
                f"| {entry['direction']} "
                f"| {entry.get('pnl', 0):.1f} "
                f"| {entry.get('allocator_score', 0):.4f} "
                f"| {entry.get('trades', 0)} |"
            )

    if violations:
        lines.append("")
        lines.append("## Violations / Notes")
        for v in violations:
            lines.append(f"- {v}")

    return "\n".join(lines)
