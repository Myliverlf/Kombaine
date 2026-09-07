# ADR-2026-08-29: Canonical Seeder Handoff

## Status

**Accepted** — Iteration 06, 2026-08-29

## Context

Prior to Iteration 06, `core/seeder.py` consumed candidates from a legacy
fixed-scan file (`futures_top5_20260818_v2.scan_results.json`), creating a
second research-to-selection truth path that conflicted with the canonical
research run contract established in Iteration 05.

The Iteration 05 run contract (`core/run_contract.py`) introduced immutable
run bundles with lifecycle tracking, integrity checks, and atomic latest-run
promotion. However, the consumer side (seeder) was not wired to consume
eligible candidates from completed runs.

**Source conflict (SOT-C02):**
- Canonical: `state/strategy_registry.json` is candidate authority
- Legacy: `core/seeder.py` loads dated fixed scan outside current architect output
- Impact: fresh research and seeding could disagree

## Decision

Introduce a **Canonical Seeder Handoff** (`core/seeder_handoff.py`) that:

### 1. Canonical Handoff Pipeline

```
LATEST CANONICAL COMPLETED RUN
→ eligible_candidates.json
→ HANDOFF VALIDATION GATE
→ SEEDER
→ CANONICAL STRATEGY REGISTRY
→ derived WATCHLIST / SIGNAL artifacts
```

### 2. Validation Gate (12 checks before admission)

1. `latest_run.json` pointer exists and parses
2. Pointed-to run directory exists
3. `manifest.json` exists with matching `run_id`
4. `status == COMPLETED`
5. All integrity checks passed (`checks.json`)
6. `eligible_candidates.json` exists and parses
7. Each candidate's `run_id` matches the run
8. Each candidate has a `config_key`
9. Each candidate's instrument is in the allowed universe
10. No duplicate `config_key`s
11. Cross-run contamination detection (step 7 above)
12. Zero partial seeding on any validation failure

### 3. Legacy Policy

- **Default:** canonical first, legacy opt-in only
- Legacy requires explicit `--use-legacy` CLI flag
- Legacy results are visibly logged with `LEGACY` label
- Legacy never masquerades as canonical
- Legacy never auto-activates after canonical failure
- Historical files need not be deleted

### 4. Idempotent Seeding

Same completed run processed twice produces no duplicate entries.
Uses `run_id + config_key` proven identity for deduplication.

### 5. Registry Boundary

```
RESEARCH → SEEDER → REGISTRY → DERIVED EXPORTS
```

- Registry remains lifecycle truth
- Derived exports (signal_pool/waitlist) are views of registry
- No direct RESEARCH → signal_pool path
- Candidates retain provenance (source_run_id, source_config_key)

### 6. Provenance

Registry records now retain where schema allows:

```json
{
  "source_type": "canonical_research_run",
  "source_run_id": "run_20260829_...",
  "source_config_key": "cfg-...",
  "source_manifest_version": "1.0.0",
  "seeded_at": "2026-08-29T..."
}
```

## Consequences

### Positive
- Eliminates SOT-C02 source conflict
- Fresh research is the only default intake path
- Legacy path remains available for debugging/migration
- Zero silent fallbacks — failures are explicit
- Same run is idempotent — safe for scheduler retry

### Negative
- Runs without a completed canonical run produce zero candidates
- Legacy users must explicitly opt in

## Evidence

- 45 tests covering T1–T16 + F1–F18
- Pre-existing 828+ tests remain green (no regressions)
- Code/ test suites (test_audit_fixes.py, test_reconcile.py) pass

## Related

- ADR-2026-08-29-canonical-research-run.md (Iteration 05)
- ADR-2026-08-29-canonical-universe-admission-gate.md (Iteration 01)
- TD-005 (P1: seeder reads dated fixed scan instead of completed research)
- TD-004 (P1: shared latest.md overwrite)

## Scope

CLASS 2: runtime non-trading. No broker, no live, no strategy changes.
