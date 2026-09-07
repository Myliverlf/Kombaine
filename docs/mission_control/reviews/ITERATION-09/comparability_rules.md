# Comparability Rules — Iteration 09

## Standard Dimensions

Two observations are comparable if they match on ALL of:
- strategy
- instrument
- timeframe
- validation_version
- cost_model_hash
- parameters (normalized JSON)

## Grouping Algorithm

Union-find with transitive closure:
1. Start with each observation as its own group
2. For each pair (i, j): if comparable, union their groups
3. Final groups are maximal comparable clusters

## Incompatible Handling

If observations are materially incompatible:
- Split into separate findings
- Record as different scope
- Do NOT average together

## Example

```
SBER/sma_cross/v1/cm1 → Group A
SBER/sma_cross/v1/cm2 → Group B (different cost model)
GAZP/sma_cross/v1/cm1 → Group C (different instrument)
```
