# Contradiction Matrix — Research-Affecting Items

**Updated:** 2026-08-30 (Iteration 23H)
**Scope:** data, research, backtest, selection, handoff, reporting
**Status legend:** ✅ FIXED | ⚠️ OPEN | 🔴 REGRESSION | ➖ N/A | ❓ UNKNOWN

---

## Matrix

| # | Area | Contradiction | Status (23H) | Evidence |
|---|------|---------------|-------------|----------|
| **C1** | **Architecture docs** | Three conflicting ARCHITECTURE.md files exist | ⚠️ OPEN | Accepted debt — docs alignment not in 23H scope |
| **C2** | **Architecture docs** | `COMBINE_SYSTEM_ARCHITECTURE.md` lives inside `docs/` | ⚠️ OPEN | Accepted debt |
| **C3** | **Data** | Universe size: arch says 5 core roots | ⚠️ OPEN | Pre-existing |
| **C4** | **Data** | LKOH 1095d duplicate unresolved | ⚠️ OPEN | Pre-existing |
| **C5** | **Data** | `data_gap_report` shows tickers outside core universe | ⚠️ OPEN | Pre-existing |
| **C6** | **Research** | `latest.md` is shared mutable | ⚠️ OPEN | Pre-existing |
| **C7** | **Research** | `DummyTimesFMAdapter` is not real ML validation | ⚠️ OPEN | Pre-existing |
| **C8** | **Backtest** | `MAX_PARAMS` inconsistency across files | ⚠️ OPEN | Pre-existing |
| **C9** | **Backtest** | `min_trades` threshold inconsistency | ✅ FIXED | Migrated to canonical policy: qualification_campaign=8, research_pipeline=8, candidate_gate=30 (stricter), quality_gate=30 (stricter) |
| **C10** | **Backtest** | `--min-trades=2` default too weak | ✅ FIXED | canonical_policy_loader enforces min_trades=8 |
| **C11** | **Selection** | Legacy write functions still exported | ⚠️ OPEN | Pre-existing |
| **C12** | **Selection** | `SCAN_RESULTS` hardcoded path in seeder | ⚠️ OPEN | Legacy path gated behind --use-legacy |
| **C13** | **Selection** | Registry events array grows unbounded | ⚠️ OPEN | Pre-existing |
| **C14** | **Handoff** | `eligible_candidates.json` does not exist on disk | ⚠️ OPEN | Pre-existing (requires completed canonical run) |
| **C15** | **Handoff** | Seeder gets 0 candidates without completed canonical run | ⚠️ OPEN | Pre-existing |
| **C16** | **Signal** | `signal_max_age_minutes=16` vs 15min tick | ⚠️ OPEN | Pre-existing |
| **C17** | **Signal** | Signal pool entries lack `run_id` provenance | ⚠️ OPEN | Pre-existing |
| **C18** | **Reporting** | combine-architecture.md describes target architecture | ⚠️ OPEN | Pre-existing |
| **C19** | **Scheduler** | ITERATION-11 says "Research: manual only" | ✅ FIXED | Timer active, documented in canonical_truth.json |
| **C20** | **Scheduler** | ADR-2026-08-30 activates daily research scheduler | ✅ FIXED | Verified active |
| **C21** | **Live Risk** | `LIVE_RISK_V1` vs code config — `max_risk_per_trade` | ✅ FIXED | qualification-time enforced via risk_policy_loader (0.25%) |
| **C22** | **Live Risk** | `LIVE_RISK_V1` vs code config — `max_gross_exposure` | ⚠️ PRE_LIVE | qualification-time aligned; runtime config.json not yet changed |
| **C23** | **Live Risk** | `LIVE_RISK_V1` vs code config — `max_concurrent_positions` | ✅ FIXED | qualification-time enforced via risk_policy_loader (1 position) |
| **C24** | **Live Risk** | `LIVE_RISK_V1` vs code config — `shorting` | ⚠️ PRE_LIVE | qualification-time BLOCKED; runtime SHORT position pre-exists |
| **C25** | **Universe** | `LIVE_RISK_V1` restricts `BR, Si` but code includes them | ⚠️ PRE_LIVE | Not addressed in 23H |
| **C26** | **Data** | `state/analytics.db` empty/legacy | ⚠️ OPEN | Pre-existing |
| **C27** | **Handoff** | Seeder double-writes: registry + signal_pool | ⚠️ OPEN | Pre-existing |
| **C28** | **Selection** | Module name collision `code/` vs `core/` | ✅ FIXED | importlib workaround |
| **C29** | **Reporting** | Multiple `latest_*` files create confusion | ⚠️ OPEN | Pre-existing |
| **C30** | **Backtest** | ARCHITECTURE.md says `initial_cash=1_000_000` | ⚠️ OPEN | Pre-existing |

---

## Summary by Area (Updated 23H)

| Area | ✅ Fixed | ⚠️ Open | 🔴 Regression | ⚠️ Pre-Live |
|------|----------|---------|---------------|-------------|
| Architecture/Docs | 0 | 2 | 0 | 0 |
| Data | 0 | 3 | 0 | 0 |
| Research | 0 | 2 | 0 | 0 |
| Backtest | 2 | 1 | 0 | 0 |
| Selection | 0 | 3 | 0 | 0 |
| Signal | 0 | 2 | 0 | 0 |
| Handoff | 0 | 3 | 0 | 0 |
| Reporting | 0 | 1 | 0 | 0 |
| Scheduler | 2 | 0 | 0 | 0 |
| Live Risk | 2 | 0 | 0 | 2 |
| Universe | 0 | 0 | 0 | 1 |
| **TOTAL** | **6** | **17** | **0** | **3** |
