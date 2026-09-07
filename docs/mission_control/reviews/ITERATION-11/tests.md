# Tests — Iteration 11

**Date:** 2026-08-30
**Status:** 85/85 GREEN

## Test categories

### T1–T23: Mandatory tests

| Test | Description | Result |
|------|-------------|--------|
| T1 | Component inventory completeness | ✅ 3 tests |
| T2 | Dependency graph | ✅ 4 tests |
| T3 | Forbidden dependency | ✅ 4 tests |
| T4 | Source-of-truth matrix | ✅ 3 tests |
| T5 | Scheduler single owner | ✅ 3 tests |
| T6 | Lock inventory | ✅ 2 tests |
| T7 | Atomic canonical write | ✅ 1 test |
| T8 | Research health | ✅ 2 tests |
| T9 | Stale research | ✅ 2 tests |
| T10 | Memory health isolation | ✅ 2 tests |
| T11 | Knowledge health — no false broker unsafe | ✅ 2 tests |
| T12 | Lifecycle health | ✅ 2 tests |
| T13 | Seeder invariant | ✅ 2 tests |
| T14 | Universe invariant | ✅ 2 tests |
| T15 | Execution journal | ✅ 2 tests |
| T16 | Analytics freshness | ✅ 2 tests |
| T17 | Disk health | ✅ 1 test |
| T18 | Read-only health | ✅ 2 tests |
| T19 | Secret redaction | ✅ 4 tests |
| T20 | Failure isolation | ✅ 2 tests |
| T21 | Idempotent health | ✅ 1 test |
| T22 | Recovery runbook | ✅ 2 tests |
| T23 | Regression | ✅ 2 tests |

### F1–F24: Failure matrix tests

| Test | Description | Result |
|------|-------------|--------|
| F1 | Config missing | ✅ |
| F2 | Invalid mode | ✅ |
| F3 | Research latest missing | ✅ |
| F4 | Research run corrupt | ✅ |
| F5 | Experiment Memory DB unavailable | ✅ |
| F6 | Knowledge DB unavailable | ✅ |
| F7 | Lifecycle DB unavailable | ✅ |
| F8 | Registry malformed | ✅ |
| F9 | Legacy seeder default enabled | ✅ |
| F10 | Foreign ticker invariant | ✅ |
| F11 | Execution journal unavailable | ✅ |
| F12 | Unresolved execution UNKNOWN | ✅ |
| F13 | Broker evidence unavailable | ✅ |
| F14 | Analytics stale | ✅ |
| F15 | Disk low | ✅ |
| F16 | Report path unwritable | ✅ |
| F17 | Duplicate scheduler owner | ✅ |
| F18 | Lock contention | ✅ |
| F19 | Component import failure | ✅ |
| F20 | Health check itself crashes | ✅ |
| F21 | Secret in diagnostic input | ✅ |
| F22 | Stale dependency chain | ✅ |
| F23 | Source-of-truth multiple writers | ✅ |
| F24 | Service alive but job stale | ✅ |

### Additional tests

| Test | Description | Result |
|------|-------------|--------|
| Snapshot generation | Full snapshot + JSON + dict serialization | ✅ 3 tests |
| Module contracts | All contracts populated, critical modules covered | ✅ 3 tests |
| Overall health | Healthy system + unsafe invariant detection | ✅ 2 tests |
| Domain health | Correct key format | ✅ 1 test |
