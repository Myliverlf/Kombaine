# Blockers — Iteration 23C
**Date:** 2026-08-30

## Active Blockers

### BLOCKER-1: Backtest Engine Bug (CRITICAL)

**Severity:** CRITICAL - blocks all strategy evidence
**Component:** run_backtest() function
**Error:** TypeError: run_backtest() missing 1 required positional argument: 'params'
**Impact:** 300/300 experiments failed (100% failure rate)
**Evidence:** experiment_memory.db, all instances with status=error
**Fix required:** Update run_backtest() call sites to pass params argument

### BLOCKER-2: SBER Not in Experiment Memory

**Severity:** HIGH - qualification universe incomplete
**Component:** experiment_memory.db
**Impact:** 0 SBER families exist; only BR (200) and GAZP (140) are indexed
**Fix required:** Seed SBER families into experiment memory

### BLOCKER-3: LKOH 1095d Data Duplicate

**Severity:** MEDIUM - blocks LKOH qualification
**Component:** Data pipeline
**Impact:** LKOH remains CONDITIONAL, excluded from live scope
**Fix required:** Resolve 1095d data condition

### BLOCKER-4: Human Authorization Not Issued

**Severity:** BY DESIGN - not a bug
**Component:** Governance
**Impact:** Live mode blocked until human authorizes
**Fix required:** Owner must issue authorization (not needed until G4 PASS)

## Resolution Priority

1. BLOCKER-1 -> Fix backtest engine -> unblocks G4
2. BLOCKER-2 -> Seed SBER -> completes qualification universe
3. BLOCKER-3 -> Resolve data -> enables LKOH
4. BLOCKER-4 -> Wait for human -> needed for Iteration 24
