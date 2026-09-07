# Experiment Planning Policy — Iteration 22

## Exploration vs Exploitation Budget

**Policy Version:** 1.0.0

| Category | Percentage | Daily Budget (250) | Purpose |
|----------|-----------|-------------------|---------|
| Novel Exploration | 30% | 75 | Test genuinely new configs |
| Revalidation | 40% | 100 | Re-test known promising evidence |
| Known-Promising Neighborhood | 30% | 75 | Test methodology/code/cost changes |

## Deterministic Plan Generation
1. Start with canonical universe × active families × parameter expansion
2. Classify each candidate against Experiment Memory
3. Allocate to budget buckets based on classification
4. Trim to daily budget limit
5. Generate deterministic plan_id from content hash

## Policy Invariants
- Same inputs → same plan (deterministic)
- Budget is bounded and documented
- No plan can exceed daily_budget
- Exact duplicates are auto-skipped (unless force_reproduction)
- Revalidation requests are honored within budget
- Zero candidates is a valid result

## Versioning
Policy version tracks changes to budget split.
Any change to percentages requires explicit version bump.
