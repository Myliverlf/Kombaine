# Tests — Iteration 15

## Summary

**76 tests: ALL PASSING**

## Mandatory Tests (T1-T24)

| Test | Description | Result |
|------|-------------|--------|
| T1 | Determinism | PASS (3 subtests) |
| T2 | Prefix invariance | PASS (3 subtests) |
| T3 | Warm-up | PASS (3 subtests) |
| T4 | Trend classification | PASS (3 subtests) |
| T5 | Volatility classification | PASS (3 subtests) |
| T6 | Stress classification | PASS (3 subtests) |
| T7 | Uncertainty | PASS (2 subtests) |
| T8 | Instrument isolation | PASS (2 subtests) |
| T9 | Timeframe isolation | PASS (1 subtest) |
| T10 | Dataset identity | PASS (1 subtest) |
| T11 | Trade mapping | PASS (1 subtest) |
| T12 | Transition mapping | PASS (1 subtest) |
| T13 | Evidence classes | PASS (2 subtests) |
| T14 | Sparse evidence | PASS (1 subtest) |
| T15 | Strategy aggregate | PASS (1 subtest) |
| T16 | Contradiction | PASS (1 subtest) |
| T17 | Knowledge integration | PASS (1 subtest) |
| T18 | Lifecycle integration | PASS (1 subtest) |
| T19 | No auto gating | PASS (1 subtest) |
| T20 | Health | PASS (3 subtests) |
| T21 | Production isolation | PASS (1 subtest) |
| T22 | Broker mutation | PASS (2 subtests) |
| T23 | Registry mutation | PASS (2 subtests) |
| T24 | Regression | PASS (3 subtests) |

## Failure Matrix (F1-F24)

All 24 failure modes tested and passing. See failure_matrix.md.

## Integration Tests

| Test | Description | Result |
|------|-------------|--------|
| Integration 1 | Full build with real data | PASS |
| Integration 2 | Current regime snapshot | PASS |
| Integration 3 | Strategy regime evidence pipeline | PASS |

## Run Command

```bash
cd /root/prop-desk/strategy_combine
python3 -m pytest tests/test_market_regime.py -v
```
