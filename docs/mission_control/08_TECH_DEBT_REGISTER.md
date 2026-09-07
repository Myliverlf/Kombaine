# Mission Control — Technical Debt Register

**Status:** VERIFIED/PARTIALLY VERIFIED inventory · 2026-08-29  
**Rule:** this register is a navigation layer. Evidence links are stronger than its prose. No listed item authorizes a change.

## Severity scale

- **P0:** could compromise broker safety, source-of-truth correctness, capital/position state, or data integrity.
- **P1:** blocks reliable repeatable production research/selection.
- **P2:** weakens maintainability, observability, or deterministic operation.
- **P3:** cleanup/documentation debt.

| ID | Severity | Subsystem | Finding | Evidence | Status | Safe next action |
|---|---|---|---|---|---|---|
| TD-001 | P0 | Execution safety | Final `Engine.post()` lacked a hard `mode/paper_first` guard. | Iteration 02 fake-broker tests/runtime proof | RESULT — final boundary VETOs unless explicit live opt-in | durable broker-order confirmation/recovery remains TD-016 |
| TD-016 | P0 | Execution recovery | No durable state distinguishes intent → submitted order → confirmed broker fill; timeout/response-loss can leave broker truth ahead of local state and replay safety is unproven. | Iteration 02 execution graph/failure matrix | VERIFIED | bounded ADR/spec for durable order-intent + broker-order-state recovery |
| TD-018 | P2 | Ranking | No portfolio-level replacement ranking existed — incumbents vs candidates compared only by single metrics | Iteration 16 ADR + 82 tests | RESULT — advisory-only ranking layer with multidimensional scoring, anti-churn, lifecycle/regime integration |
| TD-017 | P0 | Broker-state ambiguity | Broker read failure previously collapsed to `{}` semantics; could allow entry without known broker state. | Iteration 02 fault injection | RESULT — `None` unknown skips reconciliation and VETOs new entry | monitor error rate / later recovery policy |
| TD-002 | P0 | Risk / swap | Foreign `IMOEX` appeared as `swap_pending` target while configured universe is core roots. | Iteration 01 ADR/tests/runtime proof | RESULT — auto-swap/pending path gated | broader promotion/execution paths remain outside this iteration |
| TD-003 | P0 | Registry state | Registry retention/pruning is inconsistent with observed 18.5 MB registry / ~29k events; previous docs claim pruning fixed, audit says no-op. | `state/strategy_registry.json`; `docs/audit-2026-08-28.md`; production audit | CONFLICT | reproduce prune behavior in isolated test before mutation |
| TD-004 | P1 | Research provenance | Shared `reports/strategy_architect/latest.md` is overwritten by concurrent runs; top/report can mix universes. | cycle history and contaminated report incident | VERIFIED → partially addressed (Iteration 05 run contract + Iteration 06 handoff) | architect scheduler wiring remains |
| TD-005 | P1 | Candidate handoff | Seeder reads dated fixed scan instead of completed current research output. | `core/seeder.py:61`; production-cycle ADR | RESULT — canonical handoff (Iteration 06); legacy requires explicit --use-legacy opt-in | canonical eligible_candidates.json is default intake |
| TD-006 | P1 | Research coverage | Default architect grid `max_params=4` and `min_trades=2` cannot support claimed 10k robust daily selection. | `strategy_architect_autopilot.py` args | VERIFIED | make deterministic plan/gates proposal; do not alter strategy logic |
| TD-007 | P1 | Data | History archiver defaults to dynamic max 3 roots; core five-root coverage is not guaranteed by job semantics. | `tools/daily_history_archiver.py` | VERIFIED | coverage inventory / gate design |
| TD-008 | P1 | Scheduling | No single verified daily locked pipeline connects data → research → registry → report; architect Hermes job paused/error. | system timers + cron audit | VERIFIED | scheduling design only until approval |
| TD-009 | P2 | Signal lifecycle | Signal TTL is 16 min while supervisor runs every 15 min; normal scheduling drift can stale candidates. | `config.json`; supervisor flow | PARTIALLY VERIFIED | measure actual rejection logs before threshold proposal |
| TD-010 | P2 | Analytics | Root `analytics.db` is populated but `state/analytics.db` is empty; wrong-path reads are possible. | filesystem + sqlite audit | VERIFIED | document canonical path; inventory consumers |
| TD-011 | P2 | Legacy boundary | Supervisor/seeder still ingest/read legacy waitlist/signal-pool paths despite registry being canonical. | `core/supervisor.py`, `core/seeder.py`, `core/registry.py` | VERIFIED | map all consumers before retirement plan |
| TD-012 | P2 | Observability | No immutable signal-intent → risk verdict → order-intent chain was proven in Iteration 0. | architecture/code audit | UNKNOWN | focused traceability audit |
| TD-013 | P2 | Data/model claims | TimesFM adapter reported dummy fallback; it must not be represented as real model validation. | architect cycle metadata; skill audit | VERIFIED | label outputs and decide separately on integration |
| TD-014 | P3 | Documentation | Parent-level system map describes MEXC/n8n, not Tinkoff combiner; documentation can mislead operators. | `/root/prop-desk/SYSTEM_MAP.md` vs combiner code | VERIFIED | retain as separate-system doc; link scope explicitly |
| TD-015 | P3 | Configuration clarity | `max_slots` appears both under `risk` and top-level config; runtime consumer is not fully mapped here. | `config.json` | PARTIALLY VERIFIED | trace config parser and document one authority |

| TD-018 | P2 | Research intelligence | Novelty Gate not implemented: EXACT_DUPLICATE classification exists but cannot SKIP/VETO experiments | Iteration 07 ADR + tests | VERIFIED | implement novelty gate consuming classification results |
|| TD-019 | P2 | Research intelligence | Parameter-neighbor detection not implemented (near-duplicate detection) | Iteration 07 roadmap | UNKNOWN | define similarity thresholds and detection algorithm || TD-020 | P2 | Research knowledge | Knowledge decay/lifecycle policy not implemented; staleness not tracked | Iteration 09 roadmap | VERIFIED | define staleness thresholds and lifecycle transitions || TD-021 | P2 | Research knowledge | Regime detection not implemented; cannot make regime claims | Iteration 09 roadmap | RESULT — Iteration 15: deterministic regime classifier, 76 tests, prefix invariance proven, strategy regime evidence, zero mutations | regime thresholds v1.0.0 may need calibration with more data |

## Rules for closing an item

An item becomes **RESULT / closed** only when:

1. a bounded change has an accepted decision/ADR;
2. tests cover the failure condition;
3. runtime/dry-run evidence confirms the intended behavior;
4. rollback is documented where state/scheduling is affected;
5. the source-of-truth and system map records are updated.

## Evidence baseline

- `../COMBINE_SYSTEM_ARCHITECTURE.md`
- `../ADR-2026-08-29-production-research-cycle.md`
- `../audit-2026-08-28.md`
- production audit observations on 2026-08-29
- current code/config/state paths cited in the table
