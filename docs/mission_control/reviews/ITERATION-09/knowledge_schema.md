# Knowledge Schema — Iteration 09

## SQLite: state/research_knowledge.db

### research_findings

| Column | Type | Description |
|---|---|---|
| finding_id | TEXT PK | Deterministic identity |
| finding_type | TEXT | PERFORMANCE/ROBUSTNESS/etc. |
| subject | TEXT | e.g. "sma_cross:SBER" |
| scope_json | TEXT | JSON: instruments, timeframes, strategies |
| statement | TEXT | Machine-readable + human-auditable |
| status | TEXT | ACTIVE/CONTESTED/WEAKENED/etc. |
| confidence | TEXT | INSUFFICIENT/LOW/MEDIUM/HIGH |
| confidence_basis | TEXT | Explicit explanation |
| evidence_refs_json | TEXT | JSON array of EvidenceRef |
| supporting_observations | INTEGER | Count of supporting observations |
| contradicting_observations | INTEGER | Count of contradicting observations |
| first_observed_at | TEXT | ISO timestamp |
| last_updated_at | TEXT | ISO timestamp |
| last_evidence_at | TEXT | ISO timestamp |
| distiller_version | TEXT | e.g. "1.0.0" |
| schema_version | TEXT | e.g. "1.0.0" |
| knowledge_build_id | TEXT | Build provenance |

### finding_history

| Column | Type | Description |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| finding_id | TEXT | Reference to research_findings |
| previous_status | TEXT | |
| new_status | TEXT | |
| previous_confidence | TEXT | |
| new_confidence | TEXT | |
| previous_statement | TEXT | |
| new_statement | TEXT | |
| changed_at | TEXT | ISO timestamp |
| knowledge_build_id | TEXT | Build provenance |

### knowledge_builds

| Column | Type | Description |
|---|---|---|
| knowledge_build_id | TEXT PK | Deterministic from timestamp |
| started_at | TEXT | ISO timestamp |
| completed_at | TEXT | ISO timestamp |
| distiller_version | TEXT | |
| source_memory_version | TEXT | |
| findings_created | INTEGER | |
| findings_updated | INTEGER | |
| findings_unchanged | INTEGER | |
| open_questions_created | INTEGER | |
| errors_json | TEXT | JSON array |
| status | TEXT | COMPLETED/FAILED/etc. |

### open_questions

| Column | Type | Description |
|---|---|---|
| question_id | TEXT PK | Deterministic identity |
| subject | TEXT | |
| reason | TEXT | INSUFFICIENT_REVALIDATION/etc. |
| evidence_refs_json | TEXT | JSON array |
| priority_hint | TEXT | Descriptive only |
| created_at | TEXT | ISO timestamp |
| status | TEXT | OPEN/RESOLVED |
