# Failure Matrix F1–F24 — Iteration 20

| ID | Scenario | Expected Outcome | Test |
|----|----------|-----------------|------|
| F1 | Invalid approval (wrong case state) | BLOCKED | TestF01_InvalidApproval |
| F2 | Agent/system approval | BLOCKED_AGENT/SYSTEM_APPROVAL | TestF02_AgentApproval |
| F3 | Stale approval (registry drift) | BLOCKED_STALE_APPROVAL | TestF03_StaleApproval |
| F4 | Disappeared ranking (missing evidence) | MISSING_EVIDENCE_HASH | TestF04_DisappearedRanking |
| F5 | Changed identities | REGISTRY_DRIFT | TestF05_ChangedIdentities |
| F6 | Registry drift | REGISTRY_DRIFT | TestF06_RegistryDrift |
| F7 | Insufficient truth | BLOCKED_TRUTH | TestF07_InsufficientTruth |
| F8 | Missing allocation policy (zero capital) | BLOCKED_ALLOCATION | TestF08_MissingAllocationPolicy |
| F9 | Allocation violation | BLOCKED_ALLOCATION | TestF09_AllocationViolation |
| F10 | Unknown correlation | UNKNOWN (not zero) | TestF10_UnknownCorrelation |
| F11 | Open position | WAITING_FLAT, no activation | TestF11_OpenPosition |
| F12 | Unavailable flat verification | INCOMPLETE | TestF12_UnavailableFlatVerification |
| F13 | Duplicate transition | BLOCKED_CONFLICT | TestF13_DuplicateTransition |
| F14 | Concurrent conflict (same slot) | BLOCKED_CONFLICT | TestF14_ConcurrentConflict |
| F15 | Crash before deactivation | RESUME | TestF15_CrashBeforeDeactivation |
| F16 | Crash around activation | RESUME | TestF16_CrashAroundActivation |
| F17 | Registry transaction failure | ROLLBACK | TestF17_RegistryTransactionFailure |
| F18 | Reconciliation mismatch | CONFLICTED → ROLLBACK | TestF18_ReconciliationMismatch |
| F19 | Rollback failure (terminal state) | FAILED | TestF19_RollbackFailure |
| F20 | Pipeline bypass | BLOCKED | TestF20_PipelineBypass |
| F21 | Broker mutation attempt | IMPOSSIBLE | TestF21_BrokerMutationAttempt |
| F22 | Live transition attempt | BLOCKED by design | TestF22_LiveTransitionAttempt |
| F23 | Health check failure | BLOCKED_HEALTH | TestF23_HealthBlock |
| F24 | Data check failure | BLOCKED_DATA | TestF24_DataBlock |
