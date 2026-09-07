# Plan: Layered Runtime Autonomy Improvements

## Context
The suburban layered runtime (L1→L2→L3) loops without detecting convergence.
Evidence is generated from text heuristics rather than real checks. L3 executor
is a keyword scanner, not a real executor. This plan adds convergence detection,
deterministic evidence collection, and loop breaking.

## Feature 1: Deterministic Evidence Collector
- **File**: `core/evidence_collector.py` ✅ DONE
- **What**: Accepts module path + check command, runs via subprocess, returns
  structured evidence dict with stdout/returncode/timestamp. Replaces hardcoded
  evidence from l3_executor.
- **Verify**:
  ```bash
  cd /root/prop-desk/strategy_combine && python -c "
  from core.evidence_collector import collect_module_evidence
  e = collect_module_evidence('core/state_router.py', ['python', '-c', 'import core.state_router; print(\"OK\")'])
  print(e.verdict)
  assert e.verdict == 'PASS'
  print('Feature 1: PASS')
  "
  ```

## Feature 2: Convergence Detector
- **File**: `core/convergence.py` ✅ DONE
- **What**: Analyzes decisions and results from orchestration cycles. Returns
  enum CONVERGED/STUCK/PROGRESS/UNKNOWN with reason.
- **Verify**:
  ```bash
  cd /root/prop-desk/strategy_combine && python -c "
  from core.convergence import detect, Status
  d = detect(['spawned L1=n1 L2=n2 L3=n3']*4, [])
  assert d.status == Status.STUCK
  print('Feature 2: PASS')
  "
  ```

## Feature 3: Loop Breaker
- **File**: `core/loop_breaker.py` ✅ DONE
- **What**: Integrates convergence detection into spawn decisions. Returns
  should_spawn=True/False with recommendation.
- **Verify**:
  ```bash
  cd /root/prop-desk/strategy_combine && python -c "
  from core.loop_breaker import should_spawn
  d = should_spawn(['spawned L1=n1']*4, [])
  assert not d.should_spawn
  assert d.convergence_status.value == 'STUCK'
  print('Feature 3: PASS')
  "
  ```

## Feature 4: Structured Plan Output
- **File**: `code/plan.md` ✅ THIS FILE
- **What**: Concrete plan for L2 with 3-5 features (≤50 lines each) with
  verification commands. L3 receives it as input and creates files one by one.
- **Verify**:
  ```bash
  test -s code/plan.md && echo "plan.md exists and non-empty"
  ```

## Dependency Order
1. Feature 1 (evidence_collector) — no dependencies
2. Feature 2 (convergence) — no dependencies
3. Feature 3 (loop_breaker) — depends on Feature 2
4. Feature 4 (plan.md) — documentation, no code dependency

## Minimum Viable Result
Features 1+2 enable the system to collect real evidence and detect infinite loops.
Features 3+4 complete the autonomy improvement set.
