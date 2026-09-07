# 24B — Dataset Registry Design

## Canonical rule
Research consumes `dataset_id` and verified manifests, not raw CSV paths.

## New core objects
- `DatasetRegistry`
- `DatasetManifest`
- `ResearchIngressGate`

## Registry contract
- one immutable `dataset_id` → one immutable `DatasetManifest`
- schema-versioned
- verified / quarantined / invalid states are explicit
- immutable IDs must not point to different bytes

## Ingress contract
No backtest may start unless:
- instrument identity is verified
- dataset manifest exists
- provenance is verified
- identity is verified
- integrity is verified
- horizon semantics are satisfied
- manifest is frozen before outcomes
