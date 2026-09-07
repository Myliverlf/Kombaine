# Research State Machine

## Current useful states already present
- DISCOVERY
- BACKTEST_QUALIFIED
- MULTI_HORIZON_QUALIFIED
- WALK_FORWARD_QUALIFIED
- ROBUSTNESS_QUALIFIED
- RISK_QUALIFIED
- PAPER_ADMISSION_READY
- PAPER_QUALIFIED
- LIVE_CANDIDATE

## Required architectural correction
These stages must be driven by immutable experiment/promotion artifacts, not just markdown reports.

## Target explicit flow
IDEA → DISCOVERY → DEVELOPMENT → PREREGISTERED → BACKTESTING → VALIDATION_FAILED / VALIDATION_PASSED → BEHAVIORAL_DUPLICATE / RESEARCH_ROBUST → FORWARD_ELIGIBLE → FORWARD_ACTIVE → RISK_REVIEW → HUMAN_APPROVAL_REQUIRED → LIVE_ELIGIBLE → LIVE_ACTIVE → PAUSED / RETIRED

## Non-negotiable boundaries
- RESEARCH_ROBUST does not imply FORWARD_ACTIVE.
- FORWARD eligibility does not imply LIVE.
- LIVE requires explicit human authorization.
