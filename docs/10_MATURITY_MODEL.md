# 10_MATURITY_MODEL — strategy_combine

## Maturity Levels

### Level 1: Manual Research ✅
- Manual CSV downloads, manual backtest runs
- No integrity guarantees

### Level 2: Automated Pipeline ✅
- Automated data loading, strategy evaluation
- Candidate grid generation

### Level 3: Canonical Research Runs ✅
- Immutable run contracts with lifecycle tracking
- Integrity checks (12 mandatory)
- Experiment Memory with novelty gate
- Knowledge Store with findings distillation
- Lifecycle Observer for strategy health
- Atomic latest_run.json pointer

### Level 4: Controlled Paper Portfolio Execution ✅ (CURRENT)
- Single authoritative transition path
- 18-state machine with validated transitions
- APPROVE ≠ EXECUTE separation
- Deterministic allocation with risk gate
- Open position safety
- Crash recovery without blind replay
- Rollback never places real trades
- Reconciliation mandatory before COMPLETED
- Zero real broker mutation
- Paper-only execution proof

### Level 4.5: System Certification ✅ (NEW — ITERATION 21)
- Comprehensive readiness evaluation framework
- 12 readiness gates (G1-G12)
- 24 proof chains (T1-T24)
- 15 chaos tests (C1-C15)
- SQLite persistence for certification state
- Honest, evidence-based readiness decision
- Known restrictions documented
- **Readiness: CONDITIONALLY_READY**

### Level 5: Production Grade 🔄 (TARGET)
- Live execution readiness
- Multi-ticker, multi-timeframe coverage
- Portfolio allocation with risk management
- Real broker integration with safety gates

## Iteration 21 Evidence
- **Module**: core/system_certification.py (1900+ lines)
- **Tests**: 84/84 GREEN (G1-G12 + T1-T24 + C1-C15 + store + runner + dataclass)
- **Gates**: 11/12 PASS, 1 WARN
- **Proof Chains**: 23/24 PASS, 1 FAIL (data freshness)
- **Chaos Tests**: 15/15 SURVIVED
- **Overall**: CONDITIONALLY_READY
- **Restrictions**: 6 documented (data gaps, freshness, broker, Telegram, reconciliation, backfill)
- **Safety**: All invariants maintained, LIVE not activated

## Key Metrics
- **Tests**: 1971 passing total (1887 existing + 84 certification)
- **Data coverage**: 5 tickers, 15m/1h, 60d/365d/1095d (25/30 files present)
- **Strategy zoo**: 40 strategies with parameter grids
- **Registry**: 546 strategies tracked

## Last Updated
2026-08-30 — Iteration 21 End-to-End Certification
