# Authorization — Iteration 13B

**Directive:** HERMES_MISSION_CONTROL_13B_ACTIVATE_OBSERVE_CANONICAL_DAILY_SCHEDULER
**Date:** 2026-08-30
**Change class:** CLASS 2 — Controlled scheduler activation / PAPER ONLY

## Safety Verification

| Check | Value | Status |
|-------|-------|--------|
| mode | paper | ✅ |
| paper_first | true | ✅ |
| broker mutation count | 0 | ✅ |
| Eligibility hash | 1f0c6f6ed87b4285827787728c06fa3744900ae06a4c163b1e0aee5c0b94f054 | ✅ UNCHANGED |
| Daily budget | 250 | ✅ UNCHANGED |

## Authorization Scope

EXPLICITLY AUTHORIZED:
- Enable exactly one canonical daily research timer
- Prove systemd→service→coordinator→pipeline_run_id path
- Verify lock prevents concurrent invocation
- Verify health visibility
- Update runbook and evidence

NOT AUTHORIZED:
- Eligibility tuning
- Budget scaling
- Strategy changes
- Risk changes
- Live trading
- Autonomous rotation
