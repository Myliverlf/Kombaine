# Target Architecture

## Core layers
1. `InstrumentIdentity`
2. `DatasetManifest`
3. `ExperimentManifest`
4. `CampaignManifest`
5. `ResearchResult`
6. `BehavioralCluster`
7. `CandidatePromotionState`
8. `ExecutionApproval`

## Dependency direction
Instrument → Dataset → Experiment → Result → Cluster → Promotion → Execution

## Required invariants
- A ticker alone cannot enter research.
- A CSV path alone cannot establish identity.
- Every dataset must reference a verified instrument identity and immutable manifest.
- Research and execution must be hard-separated.
- Promotion states must be explicit and machine-driven.
- Reports are generated from canonical structured truth.

## Module boundary sketch
- `core/instruments/` — identity resolution and registry
- `core/data/` — dataset manifests, provenance, loaders
- `core/experiments/` — preregistration, immutable experiment records
- `core/policies/` — versioned validation and cost policies
- `core/promotion/` — state machine and gated transitions
- `research/` — pure backtesting and analytics
- `execution/` — explicitly separated live order path
