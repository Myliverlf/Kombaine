# Mission Control — Maturity Model

**Status:** evidence-backed baseline · 2026-08-29

## Scale

| Score | Meaning |
|---:|---|
| 0 | unknown / unmapped |
| 1 | exists but fragile |
| 2 | repeatable |
| 3 | controlled and tested |
| 4 | observable and recoverable |
| 5 | production-grade and evidence-backed |

A score is not a quality claim. It is the highest level supported by evidence currently collected.

## Baseline

| Subsystem | Score | Evidence | Limiting factor |
|---|---:|---|---|
| DATA | 2 | 15m timer, local continuous CSV store, long-history tools exist | core five-root long-history/freshness gate is not proven as a daily invariant |
| RESEARCH | 2 | architect runner, cycle JSON and multi-window code exist | shared mutable output; paused/error authoritative scheduling; incomplete candidate ledger |
| BACKTEST | 2 | futures_lab backtest is called by architect; metrics/equity produced | cost/leakage/contract-provenance audit not complete in Iteration 0 |
| SELECTION | 3 | canonical registry model + seeder handoff with 45 tests (Iteration 06) | no completed canonical run in production yet; architect scheduler not wired |
| WATCHLIST | 2 | registry exports waitlist/signal pool | legacy consumers still participate; provenance is incomplete |
| SIGNAL | 1 | supervisor processes pool and freshness/regime gates | no proven immutable signal-intent trace chain |
| RISK | 2 | risk config/gates plus Iteration 01 swap/pending universe gate covered by 10 tests and injected runtime proof | broader execution/risk boundaries remain unproven |
| REPLACEMENT RANKING | 3 | Iteration 16: advisory-only portfolio ranking, 82 tests T1-T24+F1-F24, SQLite store, multidimensional scoring, anti-churn, lifecycle/regime integration | correlation limited by data; policy weights require review |
| EXECUTION | 2 | Iteration 02 proves a final paper/live VETO boundary with fake broker; all mapped `strategy_combine` post calls use it | durable intent/order confirmation/restart recovery absent |
| BROKER RECONCILIATION | 2 | architecture and analytics indicate reconciliation paths | non-invasive broker truth audit not performed in Iteration 0 |
| POSITION MANAGEMENT | 2 | Iteration 01 proves foreign/unknown pending target is vetoed before retry/Engine in the auto-swap path | other close/exit and reconciliation paths remain outside this iteration |
| ANALYTICS | 2 | populated root `analytics.db` exists with trades/events | competing empty `state/analytics.db`; full broker-to-analytics lineage unproven |
| REPORTING | 1 | daily report job/scripts and charts exist | reports can mix universes through shared latest artifacts |
|| KNOWLEDGE MEMORY | 4 | two-level identity + SQLite index + 7-category classification, 58 tests (Iteration 07); Research Knowledge Layer: 8 finding types, 4 confidence levels, deterministic distiller, comparability gate, contradiction handling, query API, 106 tests (Iteration 09); Regime Detection: deterministic classifier, 76 tests, prefix invariance proven, strategy regime evidence, zero mutations (Iteration 15); read-only, no registry/novelty/trading mutations | Knowledge decay/lifecycle policy not implemented; regime thresholds v1.0.0 may need calibration; no real canonical run exercised end-to-end |
| REGIME DETECTION | 3 | Deterministic regime classifier (trend/vol/stress/confidence), 76 tests (T1-T24 + F1-F24), prefix invariance proven, instrument-local regimes, strategy regime evidence, SQLite store, zero mutations (Iteration 15) | Thresholds v1.0.0 are initial; cross-instrument/global regime not implemented; regime rotation not implemented |
| AGENT ORCHESTRATION | 1 | Hermes/Pi conventions and skills exist | canonical task-state/agent governance map is not yet established for this project |
| OPERATIONS / SCHEDULING | 2 | three enabled systemd timers observed | no one verified locked daily data→research→registry→report transaction |
| MISSION CONTROL | 2 | Iteration 01–06 constitution/map/SOT/debt/roadmap/ADR created and exercised | system map/SOT actively maintained across iterations |

## Interpretation

The system is **not maturity-3 controlled end-to-end**. It has repeatable individual subsystems, but the handoffs between research, registry, reporting and position-management safety are not yet proven as a single controlled lifecycle.

## Evidence rules for raising a score

- **1 → 2:** reproducible manual or scheduled behavior exists with artifacts.
- **2 → 3:** explicit interface/authority plus automated test covers the handoff.
- **3 → 4:** monitoring, failure state and recovery/rollback evidence exist.
- **4 → 5:** sustained operations evidence, audit trail and tested degraded-mode behavior exist.

## Re-evaluation triggers

Re-score only after one of:

- accepted bounded implementation with test/runtime evidence;
- a discovered incident or contradiction;
- a scheduled evidence review;
- a change in source-of-truth policy.

Do not raise scores merely because a document or planned component exists.

## Related records

- `01_SYSTEM_MAP.md`
- `02_SOURCE_OF_TRUTH.md`
- `08_TECH_DEBT_REGISTER.md`
- `09_ROADMAP.md`
- `../COMBINE_SYSTEM_ARCHITECTURE.md`
