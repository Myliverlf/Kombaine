# ADR-2026-08-30-clean-qualification-campaign-rebuild

## Context
23C was invalidated by a backtest contract mismatch; 23D repaired the contract but could not safely resume the old ledger.

## Decision
Create a new clean qualification campaign identity and ledger for 23E, reuse knowledge/provenance only, and keep old 23C immutable.

## Consequences
- new campaign provenance is separate from old 23C
- accounting is auditable
- infrastructure errors cannot masquerade as strategy rejection
- PAPER evidence remains required before any live candidate
