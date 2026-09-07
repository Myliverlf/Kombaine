# ADR-2026-08-30: End-to-End Certification and Live Readiness

**Date:** 2026-08-30  
**Status:** ACCEPTED  
**Iteration:** 21  
**Author:** strategy_combine certification framework

## Context

After 20 iterations of building the strategy_combine system, the project reached a point where formal readiness assessment was needed. The system has 1887 passing tests, 21 core modules, and complete paper-mode execution capability. However, no systematic evaluation of live-readiness gaps existed.

The system needs an honest, evidence-based certification process — not a checklist that always passes, but a real evaluation of what works, what's missing, and what restrictions apply.

## Decision

Implement a comprehensive certification framework (`core/system_certification.py`) that evaluates:

1. **12 Readiness Gates (G1-G12):** Critical infrastructure checks
2. **24 Proof Chains (T1-T24):** End-to-end validation chains
3. **15 Chaos Tests (C1-C15):** Resilience and failure recovery

### Startup Checklist (Always First for Long Runs)
For any long, multi-step, or token-heavy task:
1. Load the compact state dossier / state prefix (`[STATE]...[/STATE]`).
2. Set `goal`, `constraints`, `current_phase`, and `next_action` before doing broad work.
3. Search narrow first, read less, and only then execute tools.
4. Update the dossier after each major stage instead of replaying history.
5. Keep detailed logs in run artifacts, not in the prompt.

This checklist is the default operating mode for long dialogues, research runs, and orchestration loops.

The framework produces a formal readiness level:
- **NOT_READY:** Critical gates fail
- **CONDITIONALLY_READY:** All critical gates pass, restrictions documented
- **READY_FOR_CONTROLLED_LIVE:** All gates pass, no restrictions

## Evidence-Based Readiness Decision

**Overall Status: CONDITIONALLY_READY**

### Gates (11/12 PASS, 1 WARN)
| Gate | Status | Details |
|------|--------|---------|
| G1 | PASS | 1887 tests, 0 failures |
| G2 | PASS | mode=paper, paper_first=true |
| G3 | WARN | 5 data files missing (BR 1095d, Si 365d_1h, Si 1095d) |
| G4 | PASS | Registry valid, 546 strategies |
| G5 | PASS | Portfolio state valid |
| G6 | PASS | HealthChecker loads, 14 components, 9 invariants |
| G7 | PASS | Production truth schema v1.0.0 |
| G8 | PASS | PortfolioTransitionManager loads |
| G9 | PASS | RiskManager loads with config |
| G10 | PASS | Zero broker-mutating imports in certification code |
| G11 | PASS | Paper mode enforced |
| G12 | PASS | Evidence hashes computed |

### Proof Chains (23/24 PASS, 1 FAIL)
- T18 FAIL: Market data freshness — 60d data files are >7 days old
- All other chains pass

### Chaos Tests (15/15 SURVIVED)
- Config corruption recovery ✓
- DB locked recovery ✓
- Missing state files ✓
- Concurrent access ✓
- Disk full detection ✓
- Registry corruption ✓
- Process restart persistence ✓
- Network unavailable ✓
- Memory pressure ✓
- Clock skew ✓
- Empty state ✓
- Large state ✓
- Partial failure continuation ✓
- Rapid evaluation ✓
- Data path absent ✓

## Known Restrictions

1. **Data gaps:** BR 1095d (15m, 1h), Si 365d (1h), Si 1095d (15m, 1h) — 5 files missing
2. **Data freshness:** 60d files stale (>7 days) — needs fresh download
3. **Broker credentials:** TINKOFF_TOKEN present for READ access only; live order placement blocked by paper mode VETO gate
4. **No Telegram forwarding:** Alert/notify path not configured
5. **No broker reconciliation:** Cannot verify local vs broker state without live API
6. **No live data backfill:** Cannot fetch historical data without broker API credentials

## Consequences

- System is safe for continued paper-mode operation
- No live orders should be placed until restrictions are resolved
- Data gaps limit backtesting horizon for BR and Si
- The certification framework provides repeatable, auditable readiness evaluation
- Future iterations can track progress against these restrictions

## Safety Invariants Maintained

- mode=paper ✓
- paper_first=true ✓  
- Zero broker-mutation in certification path ✓
- Risk manager gates active ✓
- Human governance required for state transitions ✓
- Rollback never places real trades ✓

## CRITICAL: DO NOT ACTIVATE LIVE

This certification is evidence, not action. The system remains in paper mode.
