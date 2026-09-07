# Iteration 23J — 23F Dependency Map

## 60d
- **Type:** SHORT/MEDIUM-HORIZON RESEARCH
- Used for discovery/sanity/screening and horizon-specific validation.

## 90d
- **Type:** SHORT/MEDIUM-HORIZON RESEARCH
- Deterministic tail-slice from certified 365d coverage.

## 180d
- **Type:** SHORT/MEDIUM-HORIZON RESEARCH
- Deterministic tail-slice from certified 365d coverage.

## 365d
- **Type:** SHORT/MEDIUM-HORIZON RESEARCH
- Exact 365d data when present; may be used as source for 90/180 slices.

## 1095d
- **Type:** LONG-HISTORY QUALIFICATION
- Fail-closed unless genuine certified >=1095-day coverage exists.

## LONG_HISTORY_READY
- **Type:** LONG-HISTORY QUALIFICATION
- Required for any path that claims full 1095d qualification.

## 23F_FULL_CAMPAIGN_ALLOWED
- **Type:** PROMOTION/EXECUTION SAFETY
- Remains NO while `LONG_HISTORY_READY = NO`.
