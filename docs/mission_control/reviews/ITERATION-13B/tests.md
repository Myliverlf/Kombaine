# Tests — Iteration 13B

## Lock Fix Tests

| Test | Result |
|------|--------|
| Lock acquire (first) | ✅ ACQUIRED |
| Lock deny (second while held) | ✅ DENIED |
| Lock release | ✅ RELEASED |
| Lock re-acquire (after release) | ✅ ACQUIRED |
| Lock proof overall | ✅ PASSED |

## Systemd Integration Tests

| Test | Result |
|------|--------|
| systemd-analyze verify service | ✅ EXIT 0 |
| systemd-analyze verify timer | ✅ EXIT 0 |
| systemctl daemon-reload | ✅ EXIT 0 |
| systemctl enable --now timer | ✅ EXIT 0 |
| Timer active state | ✅ active (waiting) |
| Timer next trigger | ✅ 2026-08-30 06:00:00 CEST |
| systemctl start service (manual) | ✅ EXIT 0 |
| Service exit status | ✅ 0/SUCCESS |
| pipeline_run_id produced | ✅ 912a2975-d012-4b70-92f4-b94e46776561 |
| Manifest persisted | ✅ |
| latest.json updated | ✅ |

## Invariant Tests

| Test | Result |
|------|--------|
| mode=paper | ✅ |
| paper_first=true | ✅ |
| eligibility hash unchanged | ✅ 1f0c6f6e... |
| daily_budget=250 | ✅ |
| broker mutation = 0 | ✅ |
| execution unchanged | ✅ |

## Health Visibility Tests

| Test | Result |
|------|--------|
| scheduler component in health report | ✅ |
| timer_active=true | ✅ |
| last_pipeline_run_id visible | ✅ |
| last_run_status visible | ✅ |
| pipeline_lock_exists visible | ✅ |

## Regression

All 592 Iterations 01-13 tests remain green. Lock fix is backward-compatible.
