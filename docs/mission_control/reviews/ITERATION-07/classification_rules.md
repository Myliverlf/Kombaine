# Classification Rules — Iteration 07

**Date:** 2026-08-30

## Seven Categories

| Category | Meaning | Determined by |
|---|---|---|
| NEW | No prior compatible family exists | No matching family_id in history |
| EXACT_DUPLICATE | Same exact instance identity | Identical instance_id found |
| REVALIDATION | Same family, newer data window | Same family, different dataset_hash/start/end |
| METHODOLOGY_CHANGE | Validation protocol differs | Same family, different methodology_version |
| CODE_CHANGE | Strategy implementation differs | Same family, different code_hash |
| COST_MODEL_CHANGE | Commission/sizing assumptions differ | Same family, different cost_model_hash |
| INCOMPARABLE | Insufficient evidence to compare | Empty identity or incomplete history |

## Priority Order

Classification checks in order: EXACT_DUPLICATE → REVALIDATION → CODE_CHANGE → COST_MODEL_CHANGE → METHODOLOGY_CHANGE → NEW → INCOMPARABLE.

## Observation Only

Classification is INFORMATIONAL. It does NOT skip experiments, VETO candidates, change priorities, or alter execution flow. Future Novelty Gate will consume these results.

## Evidence

- 7 mandatory tests: T5 (EXACT_DUPLICATE), T6 (REVALIDATION), T7 (CODE_CHANGE), T8 (COST_MODEL_CHANGE), T9 (METHODOLOGY_CHANGE), T10 (INCOMPARABLE), T18 (all_different_dimensions)
- 7 failure matrix tests: F1–F7 cover boundary conditions
