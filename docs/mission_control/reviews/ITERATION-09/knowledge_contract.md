# Knowledge Contract — Iteration 09

## Finding → Evidence Chain

```
ResearchFinding
  → evidence_refs[] (EvidenceRef)
    → observation_id (experiment_instance_id)
    → run_id + config_key
    → experiment_family_id
    → Experiment Memory (state/experiment_memory.db)
      → experiment_instances table
      → experiment_families table
      → run bundle (reports/strategy_architect/runs/{{run_id}}/)
```

## Evidence Requirements

Every finding MUST have:
- finding_id (deterministic from type + subject + scope)
- evidence_refs with at least one observation
- Each evidence ref resolves to experiment_instance_id + run_id
- No finding without resolvable evidence is valid

## Finding Identity

```
finding_id = sha256(finding_type + subject + scope + methodology_version)[:16]
```

Stable across rebuilds with same evidence.

## Build Provenance

Every finding mutation is tagged with:
- knowledge_build_id
- distiller_version
- started_at / completed_at
