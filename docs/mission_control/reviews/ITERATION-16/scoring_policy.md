# Scoring Policy — Iteration 16

**Date:** 2026-08-30
**Policy Version:** 1.0.0

## Weight Distribution
| Dimension | Weight |
|---|---|
| Research quality | 25% |
| Operational evidence | 20% |
| Lifecycle health | 15% |
| Regime robustness | 15% |
| Portfolio diversification | 15% |
| Risk penalty | 10% |

## Evidence Maturity Multiplier
| Maturity | Multiplier |
|---|---|
| INSUFFICIENT | 0.3 |
| EARLY | 0.6 |
| USABLE | 0.85 |
| MATURE | 1.0 |

## Confidence Penalty
| Level | Penalty |
|---|---|
| HIGH | 0.0 |
| MEDIUM | 0.10 |
| LOW | 0.25 |
| INSUFFICIENT | 0.50 |

## Anti-Churn Margin
- Minimum score margin: 0.15
- Healthy incumbent minimum margin: 0.25
- Regime-only change threshold: 0.30

## Hard Gates (binary pass/fail)
Invalid config, missing identity, unsafe provenance, insufficient data,
stale evidence, severe contradiction (≥3), same family duplicate.
