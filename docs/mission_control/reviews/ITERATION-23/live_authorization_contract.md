# Human LIVE Authorization Contract — Iteration 23

**Date:** 2026-08-30

## Status: CONTRACT DEFINED — NOT ISSUED

### Authorization Concept
```
AUTHORIZE_CONTROLLED_LIVE_PILOT
```

### Contract Schema
```json
{
  "authorization_id": "string (UUID)",
  "human_actor_id": "string (owner identity)",
  "timestamp": "ISO-8601",
  "expiry": "ISO-8601 (must be bounded)",
  "certification_run_id": "iteration-23-certification",
  "prelive_snapshot_id": "string (snapshot hash)",
  "controlled_live_envelope_hash": "SHA256 of envelope",
  "account_id": "2042640199",
  "strategy_id": "string (exact strategy)",
  "instrument": "string (exact instrument)",
  "max_position_count": 1,
  "authoritative_risk_policy_version": "string",
  "allocation_policy_version": "string",
  "execution_policy_version": "string",
  "evidence_hash": "SHA256 of evidence bundle"
}
```

### Invalidation Conditions
Authorization becomes invalid on:
1. Expiry
2. Material change to certificate
3. Material change to snapshot
4. Material change to strategy
5. Material change to instrument
6. Material change to account
7. Material change to risk/allocation/execution policy
8. Critical health change
9. Blocking incident

**No "approve latest"** — each authorization binds to exact snapshot/state.

### Who Can Issue
- **ONLY valid HUMAN actor** (the owner)
- Agent: CANNOT issue
- System: CANNOT issue
- Telegram: CANNOT issue
- Mission Control: CANNOT issue (may prepare evidence/request only)

### Quorum Policy
**SINGLE_OWNER_EXPLICIT** — Single human owner must explicitly authorize.
No fake second human approver. Quorum must be recorded before first live pilot.

### Current State
- Contract schema: DEFINED
- Authorization issued: **NO**
- Human actor: NOT YET IDENTIFIED (owner must explicitly act)
- This directive does NOT constitute authorization

### Required Human Action (Future)
To authorize, the human owner must send in a NEW interaction:
```
AUTHORIZE_CONTROLLED_LIVE_PILOT
  snapshot_id: <prelive_snapshot_id>
  account: 2042640199
  strategy: <exact_strategy_id>
  instrument: <exact_instrument>
  max_position: 1
  expiry: <bounded_expiry>
  risk_policy: <authoritative_version>
```

This phrase must NOT be sent by agent/system/Telegram/MC.
