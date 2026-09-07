# Mission Control — Roadmap

**Status:** PROPOSED · 2026-08-29  
**Rule:** a phase is a dependency map, not standing approval to implement it. Only one bounded task is recommended at the end.

## P0 — Safety and truth

**Goal:** eliminate ambiguity that could affect broker behavior, position state or the identity of a candidate.

| Item | Dependency | Evidence of completion |
|---|---|---|
| ~~Verify/add a hard paper-mode execution guard~~ | TD-001 | **RESULT Iteration 02:** final Engine.post boundary returns VETO before fake broker call under paper/current config |
| Design durable order-intent / broker-confirmation recovery | TD-016 | accepted bounded design proves no replay after timeout/crash and separates intent/submitted/fill/position |
| ~~Reject foreign universe candidates before swap retry/promotion~~ | TD-002 | **RESULT Iteration 01:** 10 tests + injected paper proof show foreign/unknown targets are vetoed before retry/Engine; scope is auto-swap/pending only |
| Reproduce/fix registry retention only after isolated proof | TD-003 | registry event/history bounds verified on canonical data shape |
| Document root analytics DB authority and inventory consumers | TD-010 | all consumers mapped; wrong-path reads identified |

**Not included:** enabling live mode, changing slots, closing positions, changing risk limits.

## P1 — Reproducibility and canonical handoff

**Goal:** one completed research run has one unambiguous evidence bundle and one authoritative registry handoff.

| Item | Dependency | Evidence of completion |
|---|---|---|
| Immutable run directory, manifest, candidate ledger, run lock | P0 safety boundary retained | concurrent-run test proves no output overwrite |
| Explicit core-universe and coverage gate | data inventory | all allowed/blocked tickers recorded per run |
| Deterministic configuration plan / actual count reporting | immutable run bundle | plan has stable unique config keys and tested count |
| ~~Seeder migration from dated static scan to eligible artifact~~ | completed run contract | **RESULT Iteration 06:** canonical seeder handoff implemented; 45 tests; legacy opt-in only |
| One sequential daily scheduler design | handoff contract | manual paper run yields complete run-id and report |
| Run-id-specific top-N chart/report | detailed candidates persisted | renderer reproduces exact candidate configs |

## P2 — Research intelligence

**Goal:** stop re-running equivalent experiments without evidence, and turn raw runs into knowledge.

| Item | Dependency | Evidence of completion |
|---|---|---|
|| ~~Experiment identity specification~~ | P1 manifest/data hash contract | **RESULT Iteration 07:** two-level identity (family + instance), 7-category classification, 58 tests |
|| ~~Experiment memory index~~ | identity specification | **RESULT Iteration 07:** SQLite-backed memory with idempotent indexing, backfill, query API |
| Novelty / parameter-neighbor detection | detailed candidate ledger | duplicate/near-duplicate policy is measurable |
| Revalidation rules | data/regime/code version metadata | retest reason stored for each re-run |
| Knowledge distillation | evidence links | conclusions link to immutable supporting runs |

**Iteration 07 completed:** experiment identity + memory.
**Iteration 09 completed:** Research Knowledge Layer Foundation (106 tests, T1-T21 + F1-F18). Findings, confidence, contradictions, open questions, query API.
**Iteration 15 completed:** Regime Detection & Strategy Regime Evidence (76 tests, T1-T24 + F1-F24). Deterministic classifier, prefix invariance proven, strategy regime evidence, zero mutations.
Remaining P2 items: novelty gate, parameter-neighbor detection, knowledge decay/lifecycle policy.

## P3 — Architecture quality and observability

**Goal:** reduce legacy coupling and make every decision traceable.

| Item | Dependency | Evidence of completion |
|---|---|---|
| Signal → risk → execution trace schema | P0 execution truth audit | one dry-run can be reconstructed end-to-end |
| Legacy waitlist/signal-pool consumer migration plan | all consumers mapped | only derived-read compatibility remains |
| Data freshness/contract provenance dashboard | P1 manifest | per ticker/timeframe health is observable |
| Stale-lock/recovery policy | scheduler map | failed job has controlled recovery and alert |
| Documentation consolidation | Mission Control map/SOT stable | duplicate scopes are cross-linked or retired |

## P3.5 — Portfolio intelligence

**Goal:** portfolio-level decision support for strategy replacement.

| Item | Dependency | Evidence of completion |
|---|---|---|
| ~~Portfolio-level replacement ranking~~ | Iterations 09–15 evidence | **RESULT Iteration 16:** advisory-only ranking with 82 tests, 6 decision outputs, anti-churn, lifecycle/regime integration |
| Ranking-triggered human review workflow | Iteration 16 ranking | structured review task with evidence bundle |

## P4 — Governed autonomous improvement

**Goal:** make Hermes/agents improve the platform through bounded evidence-backed iterations.

| Item | Dependency | Evidence of completion |
|---|---|---|
| Agent role charter and review protocol | stable SOT/system map | each material change has independent perspectives |
| Weekly evidence review | debt/roadmap/maturity baseline | review links concrete evidence and one bottleneck |
| Architecture council templates | role charter | disagreements and accepted decision recorded |
| Automated debt/maturity collection | metrics defined | scores refresh without inventing facts |

## Phase ordering

```text
P0 truth/safety
  → P1 reproducible evidence + canonical handoff
  → P2 experiment memory
  → P3 traceability/legacy cleanup
  → P4 governed autonomy
```

## First recommended implementation task

### Task: enforce configured-universe validation for `swap_pending` before any close retry

**Reason:** a verified SBER `swap_pending` pointed to `IMOEX`, outside the configured core universe. This is the smallest P0 change that prevents a foreign research artifact from repeatedly influencing a position-management action.

**Scope:** `core/supervisor.py` plus isolated regression test and state backup procedure. No broker API call, no slot removal, no strategy/risk parameter change.

**Definition of Done:**

1. before retrying a pending swap, supervisor validates target ticker against parsed configured universe;
2. invalid pending metadata is cancelled/blocked locally and logged with reason;
3. test proves no `force_close_slot()` call occurs for an invalid target;
4. valid in-universe pending behavior stays covered;
5. test and paper dry-run pass;
6. no broker order, position, risk setting or paper/live setting changed.

**Approval required:** yes — it changes position-management control flow, even though designed as a no-broker safety guard.

## Evidence

- `08_TECH_DEBT_REGISTER.md`
- `01_SYSTEM_MAP.md`
- `02_SOURCE_OF_TRUTH.md`
- `../ADR-2026-08-29-production-research-cycle.md`
