# Failure Matrix — Iteration 09

| ID | Failure | Knowledge Mutation | Build Status | Recovery | Memory Changes | Research Changes | Trading Changes |
|---|---|---|---|---|---|---|---|
| F1 | Knowledge DB missing | None | FAILED_NO_MEMORY | Create DB | None | None | None |
| F2 | Schema init fails | None | FAILED_NO_MEMORY | Auto-create | None | None | None |
| F3 | Experiment Memory unavailable | None | FAILED_NO_MEMORY | Check path | None | None | None |
| F4 | No observations exist | None | COMPLETED_EMPTY | Normal | None | None | None |
| F5 | Observation provenance missing | None | COMPLETED | Evidence refs have empty fields | None | None | None |
| F6 | Incompatible observations | Split into separate findings | COMPLETED | Separation | None | None | None |
| F7 | Metric missing | metric_missing tracked | COMPLETED | Normal | None | None | None |
| F8 | Contradictory observations | CONTESTED finding | COMPLETED | Normal | None | None | None |
| F9 | Duplicate reproduction inflation | Unique instance_id counted | COMPLETED | Normal | None | None | None |
| F10 | Distiller crashes mid-build | FAILED_MEMORY_QUERY | FAILED | Re-run | None | None | None |
| F11 | Finding write fails | None | FAILED | Check permissions | None | None | None |
| F12 | Evidence link broken | FINDING_NOT_FOUND | N/A | Query returns error | None | None | None |
| F13 | Schema migration required | Idempotent schema | COMPLETED | Re-run | None | None | None |
| F14 | Source memory changes during build | Snapshot at start | COMPLETED | Re-run | None | None | None |
| F15 | Zero eligible but negative evidence | FAILURE_PATTERN finding | COMPLETED | Normal | None | None | None |
| F16 | Legacy/incomparable evidence | Handled | COMPLETED | Normal | None | None | None |
| F17 | Missing finding explanation | FINDING_NOT_FOUND error | N/A | Check ID | None | None | None |
| F18 | Rebuild executed twice | Idempotent | COMPLETED | Same findings | None | None | None |

**Key invariant:** Knowledge failure NEVER alters trading, research execution, or registry state.
