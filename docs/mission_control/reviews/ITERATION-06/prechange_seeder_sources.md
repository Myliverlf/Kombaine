# Pre-Change Seeder Source Inventory

**Date:** 2026-08-29 20:10 UTC
**Status:** AUDIT

## Source Inventory

| Source | Path | Producer | Consumer | Mutability | Status |
|--------|------|----------|----------|------------|--------|
| Fixed scan file | `strategies/futures_top5_20260818_v2.scan_results.json` | strategy_architect_autopilot | core/seeder.py | Immutable file | LEGACY |
| Strategy registry | `state/strategy_registry.json` | core/seeder.py, code/strategy_registry.py | core/supervisor.py, seeder | Mutable JSON | CANONICAL |
| Signal pool | `state/signal_pool.json` | code/strategy_supervisor_flow.py (derived) | core/supervisor.py | Derived view | DERIVED |
| Waitlist | `state/waitlist.json` | code/strategy_supervisor_flow.py (derived) | core/supervisor.py | Derived view | DERIVED |
| Latest run pointer | `reports/strategy_architect/latest_run.json` | core/run_contract.py | NEW: core/seeder_handoff.py | Atomic JSON | CANONICAL |
| Eligible candidates | `reports/strategy_architect/runs/{run_id}/eligible_candidates.json` | core/run_contract.py | NEW: core/seeder_handoff.py | Immutable per-run | CANONICAL |

## Conflicts Identified

1. **SOT-C02 (resolved):** seeder consumed fixed scan instead of canonical run
2. Legacy scan path competed with canonical registry as intake source
3. No validation gate between research output and seeder intake

## Classification

- `futures_top5_20260818_v2.scan_results.json`: **LEGACY** — replaced by canonical handoff
- `latest_run.json` → `eligible_candidates.json`: **CANONICAL** — new default
- `strategy_registry.json`: **CANONICAL** — lifecycle truth retained
- `signal_pool.json` / `waitlist.json`: **DERIVED** — views of registry
