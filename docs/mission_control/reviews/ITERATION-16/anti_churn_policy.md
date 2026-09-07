# Anti-Churn Policy — Iteration 16

**Date:** 2026-08-30

## Purpose
Prevent unnecessary strategy turnover from small score fluctuations.

## Mechanisms
1. **Minimum score margin**: candidate must exceed incumbent by ≥0.15
2. **Healthy incumbent protection**: margin increases to ≥0.25
3. **Lifecycle-aware barrier**: HEALTHY incumbent → stronger evidence needed
4. **Regime-only threshold**: regime advantage alone below 0.30 → no replacement

## Decision Flow
```
candidate_score - incumbent_score < min_margin → KEEP
candidate_score - incumbent_score ≥ min_margin → evaluate further
incumbent HEALTHY → require min_healthy_incumbent_margin
incumbent DECAY → lower barrier
```

## Explanation
Anti-churn decisions include reason code `ANTI_CHURN_PROTECTED` with
explanation of why the margin was insufficient.
