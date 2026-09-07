# 08_TECH_DEBT_REGISTER — strategy_combine

## Active Tech Debt

### TD-001: strategy_lifecycle.py dict-vs-string registry handling
- **Severity**: Medium
- **Status**: FIXED (2026-08-29)
- **Description**: `build_snapshot()` called `s.get("status")` on string entries in registry strategies list
- **Fix**: Added isinstance checks; strings treated as "active", non-dict entries skipped in evaluation
- **File**: core/strategy_lifecycle.py lines 1298-1310

### TD-002: scorecard_dryrun import error
- **Severity**: Low
- **Status**: Open
- **Description**: `from risk_scorecard import validate_constraints` fails — function moved/renamed
- **Impact**: 10 test errors in test_scorecard_dryrun_real.py
- **File**: tests/test_scorecard_dryrun.py:30

### TD-003: pandas FutureWarning on downcasting
- **Severity**: Low
- **Status**: Open
- **Description**: strategy_zoo.py uses deprecated `.fillna()` pattern
- **Impact**: ~12 warnings per run
- **File**: strategy_zoo.py lines 557-558, 660-661

### TD-004: Market data gaps (Iteration 21 certification)
- **Severity**: Medium
- **Status**: Open
- **Description**: 5 data files missing: BR_1095d (15m, 1h), Si_365d_1h, Si_1095d (15m, 1h)
- **Impact**: Cannot backtest BR/Si with full horizon coverage
- **Resolution**: Download via broker API (requires TINKOFF_TOKEN)

### TD-005: Market data freshness (Iteration 21 certification)
- **Severity**: Low
- **Status**: Open
- **Description**: 60d data files are >7 days old
- **Impact**: Backtests use stale data
- **Resolution**: Run data download timer

## Resolved Tech Debt

### TD-R001: Iteration 12 sys.path bug
- **Resolved**: 2026-08-29
- **Description**: futures_lab not on sys.path before import, run_backtest wrong args
- **Fix**: Added sys.path.insert(0, futures_lab_root) at script top, corrected call signature

## Last Updated
2026-08-30 — Iteration 21 End-to-End Certification
