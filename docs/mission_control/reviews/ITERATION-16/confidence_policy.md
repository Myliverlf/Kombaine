# Confidence Policy — Iteration 16

**Date:** 2026-08-30

## Confidence Levels
HIGH | MEDIUM | LOW | INSUFFICIENT

## Factors
- Evidence maturity (INSUFFICIENT/EARLY/USABLE/MATURE)
- Attribution confidence (present/absent)
- Regime coverage (present/absent)
- Data freshness (<24h / <7d / >30d)
- Cross-source consistency
- Contradiction count

## Score Computation
Confidence score = maturity_score*2 + attribution*1.5 + regime*1.0 + freshness*1.0 + consistency_penalty - contradictions*0.5

- HIGH: score ≥ 6
- MEDIUM: score ≥ 4
- LOW: score ≥ 2
- INSUFFICIENT: score < 2

## Key Principle
Score and confidence are SEPARATE. A high-scoring candidate with low confidence
is treated cautiously.
