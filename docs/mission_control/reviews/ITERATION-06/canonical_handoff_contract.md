# Canonical Handoff Contract

**Date:** 2026-08-29 20:10 UTC

## Pipeline

```
latest_run.json (atomic pointer)
→ run_dir (from pointer path)
→ manifest.json (verify run_id match, status == COMPLETED)
→ checks.json (verify all_passed == true)
→ eligible_candidates.json (load, parse, validate each candidate)
→ HANDOFF VALIDATION GATE
→ seed_from_eligible() → registry
→ registry.save() → export_legacy_state_files()
```

## Validation Gate Checks (in order)

| # | Check | Failure Action |
|---|-------|----------------|
| 1 | latest_run.json exists and parses | HANDOFF_BLOCKED (F1/F2) |
| 2 | run directory exists | HANDOFF_BLOCKED (F3) |
| 3 | manifest.json exists, run_id matches | HANDOFF_BLOCKED (F4) |
| 4 | status == COMPLETED | HANDOFF_BLOCKED (F5/F6) |
| 5 | checks.json exists, all_passed == true | HANDOFF_BLOCKED (F7) |
| 6 | eligible_candidates.json exists, parses as list | HANDOFF_BLOCKED (F8/F9) |
| 7 | Each candidate run_id matches run | REJECT candidate (F10) |
| 8 | Each candidate has config_key | REJECT candidate (F11) |
| 9 | No duplicate config_keys | REJECT candidate (F12) |
| 10 | Each candidate instrument in universe | REJECT candidate (F13) |

## Hard Invariants

- INVALID CANONICAL INPUT ≠ SILENT LEGACY FALLBACK
- Zero partial seeding on validation failure
- Cross-run contamination blocks entire batch
- Same run + config_key seeds idempotently
- Registry remains lifecycle truth after seeding
