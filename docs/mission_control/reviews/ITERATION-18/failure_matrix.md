# Failure Matrix — F1-F24

| ID | Failure | Mitigation | Test |
|----|---------|------------|------|
| F1 | mission_control.db unavailable | Auto-recreate, corrupt recovery | TestF1DBUnavailable |
| F2 | source health unavailable | Graceful fallback to UNKNOWN | TestF2SourceHealthUnavailable |
| F3 | research status missing | Graceful fallback | TestF3ResearchStatusMissing |
| F4 | lifecycle missing | Graceful fallback | TestF4LifecycleMissing |
| F5 | ranking missing | Graceful fallback | TestF5RankingMissing |
| F6 | human review unavailable | Graceful fallback | TestF6HumanReviewUnavailable |
| F7 | duplicate cycle | Lock prevents concurrent cycles | TestF7DuplicateCycle |
| F8 | stale lock | 30-min stale recovery | TestF8StaleLock |
| F9 | duplicate task | Payload hash deduplication | TestF9DuplicateTask |
| F10 | task crash | Failed state detection | TestF10TaskCrash |
| F11 | task stuck RUNNING | Visible in pending tasks | TestF11TaskStuckRunning |
| F12 | revalidation duplicate | Dedupe check | TestF12RevalidationDuplicate |
| F13 | revalidation unsupported | Advisory validation | TestF13RevalidationUnsupported |
| F14 | revalidation recursion | Loop guard (max 3/period) | TestF14RevalidationRecursion |
| F15 | incident duplicate | Fingerprint merge | TestF15IncidentDuplicate |
| F16 | recovery attempt fails | Recorded, escalation available | TestF16RecoveryAttemptFails |
| F17 | recovery exceeds max | Blocked after max attempts | TestF17RecoveryMaxAttempts |
| F18 | notifier unavailable | Alert stored locally | TestF18NotifierUnavailable |
| F19 | notifier spam loop | Rate limiting (10/cycle) | TestF19NotifierSpamLoop |
| F20 | secret in alert | Automatic redaction | TestF20SecretInAlert |
| F21 | agent attempts human approval | No APPROVE/REJECT action exists | TestF21AgentCannotApprove |
| F22 | control plane attempts registry mutation | Audit check | TestF22NoRegistryMutation |
| F23 | control plane attempts broker mutation | Audit check | TestF23NoBrokerMutation |
| F24 | fixture data leaks into production | Isolated tmp_path tests | TestF24NoFixtureLeakage |
