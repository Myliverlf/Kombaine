# ADR-2026-08-29: Canonical Research Run Contract

## Status

**Accepted** — Iteration 05, 2026-08-29

## Context

strategy_combine's research pipeline (strategy_architect_autopilot) runs daily
backtests across instruments, timeframes, and strategy families. Prior to
Iteration 05, research runs had no structured lifecycle — outputs were
untracked, reproducibility was impossible, and there was no integrity barrier
between raw backtest output and "latest eligible" promotion to the registry.

## Decision

Introduce a **Canonical Research Run Contract** (`core/run_contract.py`) that
enforces:

### 1. Immutable Run Identity

Every research cycle produces a uniquely identified run bundle under
`reports/strategy_architect/runs/{run_id}/`. The run_id encodes timestamp +
random suffix for collision safety. Runs are append-only directories.

### 2. Lifecycle State Machine

Runs follow an immutable state progression:

```
PLANNED → RUNNING → COMPLETED
                  → PARTIAL (integrity checks failed)
                  → FAILED
         → BLOCKED (lock contention)
```

Terminal states (COMPLETED, FAILED, PARTIAL, BLOCKED) have no outgoing
transitions. State transitions are validated and raise `ValueError` on
illegal moves.

### 3. Write-Ahead Manifest

Before heavy execution begins, `start_planning()` persists a `manifest.json`
capturing: universe, timeframes, horizons, strategy families, git revision,
code version, cost assumptions, and host context. This manifest is the
audit trail.

### 4. Append-Safe Candidate Ledger

Every attempted configuration produces exactly one terminal record in
`candidates.jsonl` (JSONL format). The manifest tracks:
- `planned_configurations` — from research plan grid
- `tested_configurations` — successful backtests
- `failed_configurations` — error cases
- `eligible_configurations` — candidates passing quality gates

### 5. 12-Point Integrity Check

Before a run can become COMPLETED, all 12 mandatory checks must pass:
manifest exists, plan exists, ledger exists, planned/tested reconcile,
every planned config has a terminal record, eligible subset of ledger,
report run_id matches manifest, data fingerprints exist, code identity
exists, cost assumptions exist, run status valid, no secrets detected.

### 6. Atomic Latest Pointer

Only COMPLETED runs can update `latest_run.json` via atomic rename. This
is the canonical handoff to the seeder/registry pipeline.

### 7. Lock-Based Exclusivity

A file-lock (`fcntl.flock`) in the state directory prevents concurrent
research runs. Stale locks from dead PIDs are auto-recovered.

## Consequences

- **Reproducibility**: Every run is self-contained — manifest + plan +
  candidates + eligible + report + checks. Any run can be re-analyzed
  months later.

- **Safety**: The integrity check barrier prevents incomplete or corrupted
  runs from becoming the "latest" eligible source for live trading.

- **Auditability**: Secret detection, data fingerprinting, and code identity
  capture make every run a complete audit artifact.

- **No Breaking Changes**: The contract is additive — existing pipeline
  scripts can adopt `ResearchRun` incrementally.

## Alternatives Considered

1. **In-memory-only runs**: Rejected — no audit trail, no reproducibility.
2. **Database-backed runs**: Rejected for now — JSONL files are simpler
   for a single-user research pipeline; SQLite can be added later if needed.
3. **Git-based run tracking**: Too heavy for daily run artifacts.

## Test Coverage

- **T1–T14**: 14 mandatory functional tests covering every lifecycle phase
- **F1–F12**: 12 failure matrix tests covering invalid transitions, missing
  artifacts, secret detection, integrity failures, and edge cases
- **Total**: 35 tests in `tests/test_run_contract.py`
