# Mission Control Policy — v1.0.0

## Priority Order (deterministic, lower = higher priority)

| Priority | Name | Trigger | Action |
|----------|------|---------|--------|
| P0 | SAFETY | UNSAFE health or SAFETY_STOP | ESCALATE_OPERATOR |
| P1 | DATA_FAILURE | Research failed/unknown/blocked, attribution blocked, knowledge missing | OPEN_INCIDENT |
| P2 | FAILED_TASK | Failed control-plane tasks > 0 | RESUME_FAILED_TASK |
| P3 | REQUIRED_REVALIDATION | Overdue revalidations > 0 or pending > 0 | CREATE_REVALIDATION |
| P4 | RESEARCH_NEED | Research stale | RUN_CANONICAL_RESEARCH |
| P5 | HUMAN_REVIEW | Replacement candidates with no open review | REQUEST_HUMAN_REVIEW |
| P6 | WAIT_FOR_EVIDENCE | Decay suspected, awaiting evidence | WAIT_FOR_EVIDENCE |
| P7 | NO_ACTION | All systems nominal | NO_ACTION |

## Policy Version
MC_POLICY_VERSION = "1.0.0"
Created: 2026-08-30
Authoritative: Yes (deterministic, no LLM dispatch)
