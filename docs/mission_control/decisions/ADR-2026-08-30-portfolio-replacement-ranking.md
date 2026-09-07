# ADR-2026-08-30-portfolio-replacement-ranking

**Date:** 2026-08-30
**Status:** ACCEPTED
**Iteration:** 16
**Change class:** CLASS 2 — Decision-support analytics / NON-AUTHORITATIVE

## Context

Iterations 01–15 established: canonical research, experiment memory, novelty gate,
research knowledge, strategy lifecycle, production performance attribution,
regime detection, and system health. The missing Phase 6 capability was
portfolio-level comparison of incumbent vs candidate strategies to determine
whether replacement consideration is warranted.

## Decision

Build a deterministic, evidence-aware, advisory-only replacement ranking layer
that compares incumbents and candidates across research, operational, lifecycle,
regime, and portfolio dimensions.

## Boundary

### Advisory-only
- RANKING ≠ SWAP ≠ ORDER ≠ ELIGIBILITY MUTATION
- No automatic swap, activation, deactivation, or registry mutation
- All outputs are advisory: KEEP / WATCH / REVALIDATE / REPLACEMENT_CANDIDATE / INSUFFICIENT_EVIDENCE / NO_VALID_CANDIDATE

### Read-only
- Reads: registry, performance attribution, lifecycle, regime, research knowledge
- Writes: state/replacement_ranking.db (DERIVED, never becomes registry truth)
- NEVER: registry mutations, swap mutations, broker calls, trading, risk changes

## Candidate Source

Canonical from strategy registry:
- Active candidates: `registry/candidate`, `waitlist` statuses
- Excluded: `rejected`, `rotated_out`, `expired`, `conflicted`

## Evidence Dimensions

| Dimension | Source | Weight |
|---|---|---|
| Research quality | Backtest metrics (PF, Sharpe, win rate, drawdown, sample size) | 25% |
| Operational evidence | Paper/BROKER_REAL trades, attribution confidence | 20% |
| Lifecycle health | strategy_lifecycle observer output | 15% |
| Regime robustness | market_regime regime coverage and performance | 15% |
| Portfolio contribution | Overlap, correlation, diversification benefit | 15% |
| Risk penalty | Drawdown, concentration, slot pressure | 10% |

## Hard Gates vs Soft Score

Hard gates (candidate cannot win by score if failed):
- INVALID_CONFIG, MISSING_IDENTITY, UNSAFE_PROVENANCE
- INSUFFICIENT_DATA, STALE_EVIDENCE, SEVERE_CONTRADICTION
- SAME_FAMILY_DUPLICATE

Soft scores are multidimensional — no single-metric winner.

## Confidence

Confidence depends on: evidence maturity, attribution quality, regime coverage,
data freshness, cross-source consistency. Score and confidence are separate.

## Anti-Churn

- Minimum score margin required for replacement consideration
- Healthy incumbent requires stronger evidence margin (0.25 vs 0.15)
- Regime-only change below threshold does not trigger replacement

## Lifecycle Integration

- HEALTHY incumbent → stronger evidence required
- DECAY_CONFIRMED incumbent → lower barrier, but still advisory
- No automatic swap on decay detection

## Regime Integration

- Current regime is one evidence dimension, not a command
- Regime matching provides small score bonus, never auto-gates
- Strategy should not be ranked first solely on current regime match

## Portfolio Overlap

Measured via: same ticker, same family, same directional exposure,
return correlation (when aligned data exists), regime concentration.
Correlation only computed when ≥30 aligned observations available.

## Negative Evidence

Explicitly included: drawdown, loss streak, poor PF, decay,
regime-specific failure, contradictions, stale evidence.

## Duplicate Evidence

Repeated experiments with same config/data do not inflate confidence.
Evidence counted as correlated, not independent.

## Counterfactual

Simulated portfolio comparisons labeled `SIMULATED / NON-REALIZED`.
Never presented as realized performance.

## Replacement Margin

- `candidate_advantage > minimum_replacement_margin` required
- Margin considers: score difference, confidence, evidence maturity,
  portfolio diversification benefit, incumbent decay status

## Store

SQLite-backed: `state/replacement_ranking.db`
Tables: ranking_builds, portfolio_snapshots, replacement_comparisons,
candidate_scores, ranking_explanations
Schema version: 1.0.0

## Policy Version

`RANKING_POLICY_VERSION = "1.0.0"`
Weights, gates, penalties are policy, not truth. Versioned explicitly.

## Consequences

### Positive
- Portfolio-level decision support for strategy replacement
- Multidimensional, explainable, auditable ranking
- Anti-churn protection prevents unnecessary strategy turnover
- Lifecycle-aware: healthy incumbents protected
- Regime-aware without regime-gating

### Negative
- Additional derived store to maintain
- Policy weights require periodic review
- Correlation computation limited by data availability
- Advisory only — human review still required for actual swaps
