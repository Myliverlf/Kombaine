# 24B — Research Ingress Audit

## Legacy direct inputs audited
- ticker strings
- CSV/file paths
- raw DataFrames
- alias-style synthetic labels

## Status
- ticker direct access: blocked by gate for normal research entrypoints
- CSV direct access: blocked by gate for normal research entrypoints
- DataFrame direct access: blocked by gate for normal research entrypoints

## Remaining work
Some legacy scripts still exist and should be migrated or deprecated, but the canonical research path now has a hard ingress gate.
