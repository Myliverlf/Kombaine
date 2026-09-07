# Technical Debt Register

## CRITICAL
1. **Identity-by-filename risk** — file paths can be mistaken for instrument identity.
2. **Fragmented canonical truth** — reports, registry, policy, and scripts all hold partial truth.
3. **Unverified dataset provenance** — research can proceed on artifacts whose lineage is weaker than desired.

## HIGH
4. **Behavioral duplicate inflation** — correlated variants can overstate evidence.
5. **Campaign logic scattered** — multiple scripts and campaign modes overlap.
6. **Promotion semantics coupling** — research/forward/live boundaries are not uniformly enforced.

## MEDIUM
7. **Legacy scripts and shims** — old utilities still coexist with newer contracts.
8. **Policy versioning gaps** — some thresholds are versioned, others are hardcoded in scripts.

## LOW
9. **Report synchronization burden** — markdown and JSON can drift if not generated from canonical state.
