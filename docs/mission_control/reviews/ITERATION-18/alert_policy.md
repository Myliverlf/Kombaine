# Alert Policy — v1.0.0

## Alert Event Types
- MC_DECISION: Decision made in a cycle
- TASK_CREATED/STARTED/COMPLETED/FAILED: Task lifecycle
- REVALIDATION_CREATED/COMPLETED: Revalidation lifecycle
- INCIDENT_OPENED/ESCALATED/RESOLVED: Incident lifecycle
- HUMAN_REVIEW_REQUIRED: Human review case created
- SYSTEM_BLOCKED: System blocked, operator attention needed

## Rate Limiting
- ALERT_COOLDOWN_SECONDS = 300 (5 minutes per fingerprint)
- MAX_ALERTS_PER_CYCLE = 10
- Fingerprint = SHA256(event_type:source_component:reason_code)[:16]

## Secret Redaction
- Token patterns: token/key/secret/password/api_key = ***REDACTED***
- Long strings (>20 chars): ***REDACTED_TOKEN***
- Applied before storage and before any notification

## Deduplication
- Same fingerprint within cooldown = suppressed
- Suppressed alerts recorded with suppression_reason
- Resolution notification sent on incident RESOLVED

## Telegram Integration
- SAFETY/BLOCKING incidents → notification
- Persistent DEGRADED incidents → notification
- Human review required → notification
- MC escalation → notification
- Revalidation failure after retries → notification
- NO secrets/tokens in notifications
- Telegram response is NOT approval (approval = Iteration 17 only)
