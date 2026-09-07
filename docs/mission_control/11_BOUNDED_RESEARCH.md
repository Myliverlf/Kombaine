# Mission Control — Bounded Research State (Iteration 23J)

**Status:** ACTIVE / DERIVED OPERATIONAL STATE
**Canonical truth remains unchanged:**
- `RESEARCH_TRUTH_READY = YES`
- `LONG_HISTORY_READY = NO`
- `23F_FULL_CAMPAIGN_ALLOWED = NO`

## Definition
`BOUNDED_RESEARCH_ALLOWED = YES` means:

> Research that does not require long-history qualification may execute on certified available horizons.

It is a derived operational permission, not a qualification upgrade.

## What it permits
- use certified available horizons only:
  - 60d
  - 90d
  - 180d
  - 365d
- run bounded research, screening, diagnostics, and rank/reject experiments
- record long-history pending states for candidates that would otherwise need 1095d

## What it does not permit
- it does **not** imply `LONG_HISTORY_READY = YES`
- it does **not** imply `23F_FULL_CAMPAIGN_ALLOWED = YES`
- it does **not** imply `FULLY_QUALIFIED = YES`
- it does **not** imply `EXECUTION_ELIGIBLE = YES`
- it does **not** relax 1095d certification
- it does **not** allow synthetic or substituted history

## Rejection rule
No strategy may move from bounded validation to full qualification if the required genuine 1095d check has not passed.

## Reopen rule
This state may be revisited only if canonical long-history truth changes or the owner explicitly changes the specification.
