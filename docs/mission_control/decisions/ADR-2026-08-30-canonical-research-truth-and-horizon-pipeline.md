# ADR-2026-08-30 — Canonical Research Truth and Horizon Pipeline

## Decision
Use one canonical architecture, policy, horizon resolver, handoff and registry truth. Treat old docs and contaminated result ledgers as historical/non-authoritative.

## Consequences
90/180 are deterministic slices; nominal 1095 with insufficient actual coverage fails closed. Research truth is not ready until risk/threshold/loader/legacy-consumer drift is closed.

## Safety
No broker mutations, no LIVE authorization, mode remains paper.
