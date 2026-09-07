# Current Architecture — Trading Combine

## What it currently is
A research-first trading combine with several partially overlapping truth stores and contracts:
- `core/horizon_resolution.py` enforces fail-closed horizon selection and checksum-backed CSV resolution.
- `core/stage_machine.py` defines a linear candidate qualification chain and evidence contracts.
- `core/research_handoff.py` and `core/run_contract.py` enforce immutable run/hand-off artifacts.
- `core/eligible_candidates_contract.py` makes eligible-candidate artifacts immutable.
- `code/qualification_campaign.py` still acts as the main research campaign engine.
- Mission-control markdown and JSON reports remain important views, but they are not the only truth store.

## Current data flow
Ticker / registry label
→ CSV-path or synthetic file alias
→ horizon resolver
→ DataFrame
→ backtest / strategy zoo
→ campaign gating
→ report artifacts

## Current truth stores
- `state/strategy_registry.json`
- `docs/mission_control/canonical_truth.json`
- `config/research_qualification_policy.json`
- `reports/**`
- `state/**`
- `core/*_contract.py`

## Main gap
The system already has pieces of a canonical pipeline, but identity, dataset lineage, experiment manifests, and promotion states are not unified into one immutable machine-readable graph.
