# Tests — Iteration 09

## Test Count: 106

### Mandatory Tests (T1–T21)

| Test | Description | Result |
|---|---|---|
| T1 | Finding identity: same type/scope produces stable ID | PASS (6 subtests) |
| T2 | Evidence provenance: every finding resolves to observations | PASS (3 subtests) |
| T3 | Empty memory: zero observations → valid empty build | PASS (2 subtests) |
| T4 | Comparable aggregation: compatible observations aggregate deterministically | PASS (2 subtests) |
| T5 | Incompatible separation: different observations not averaged | PASS (5 subtests) |
| T6 | Low evidence: single observation cannot become HIGH confidence | PASS (2 subtests) |
| T7 | Confidence basis: every confidence has explicit basis | PASS (4 subtests) |
| T8 | Contradiction: opposing evidence → contested status | PASS (2 subtests) |
| T9 | Negative evidence: rejected research visible | PASS (2 subtests) |
| T10 | No survivorship bias: not limited to eligible | PASS (2 subtests) |
| T11 | Duplicate reproduction: not inflated | PASS (2 subtests) |
| T12 | Missing metric: not converted to zero | PASS (2 subtests) |
| T13 | Build provenance: every mutation has build_id | PASS (2 subtests) |
| T14 | Idempotent rebuild: same evidence → same findings | PASS (1 subtest) |
| T15 | Finding history: material changes preserved | PASS (1 subtest) |
| T16 | Query interface: all queries work | PASS (8 subtests) |
| T17 | Open questions: structured unresolved gaps | PASS (3 subtests) |
| T18 | No registry mutation: knowledge cannot change registry | PASS (2 subtests) |
| T19 | No novelty-policy mutation: knowledge cannot change skip policy | PASS (2 subtests) |
| T20 | No trading authority: no broker/execution/risk | PASS (3 subtests) |
| T21 | Regression: iterations 01-08 imports work | PASS (3 subtests) |

### Failure Matrix Tests (F1–F18)

| Test | Description | Result |
|---|---|---|
| F1 | Knowledge DB missing | PASS (2 subtests) |
| F2 | Schema initialization | PASS (2 subtests) |
| F3 | Experiment Memory unavailable | PASS (2 subtests) |
| F4 | No observations exist | PASS (1 subtest) |
| F5 | Observation provenance missing | PASS (1 subtest) |
| F6 | Incompatible observations | PASS (1 subtest) |
| F7 | Metric missing | PASS (1 subtest) |
| F8 | Contradictory observations | PASS (1 subtest) |
| F9 | Duplicate inflation | PASS (1 subtest) |
| F10 | Distiller crashes mid-build | PASS (1 subtest) |
| F11 | Finding write fails | PASS (1 subtest) |
| F12 | Evidence link broken | PASS (2 subtests) |
| F13 | Schema migration required | PASS (1 subtest) |
| F14 | Source memory changes during build | PASS (1 subtest) |
| F15 | Zero eligible but negative evidence | PASS (1 subtest) |
| F16 | Legacy/incomparable evidence | PASS (1 subtest) |
| F17 | Missing finding explanation | PASS (1 subtest) |
| F18 | Rebuild executed twice | PASS (2 subtests) |

### Additional Tests

| Category | Count | Result |
|---|---|---|
| Confidence model | 4 | ALL PASS |
| Comparability | 3 | ALL PASS |
| KnowledgeStore | 4 | ALL PASS |
| Finding types/statuses | 6 | ALL PASS |
| Edge cases | 3 | ALL PASS |
| EvidenceRef serialization | 3 | ALL PASS |

**Total: 106 tests, ALL PASSING**
