# Data Lineage

## Current lineage pattern
Most current research data is resolved from local continuous CSV files under `futures_lab/artifacts/tinkoff_futures_data/`.

## Problem
The current filenames encode nominal horizon and asset labels, but those labels are not sufficient as identity proof.

## Target lineage
Provider → instrument identity → raw acquisition → normalized dataset → derived/continuous dataset → experiment reference → result artifact

## Must be recorded
- provider
- provider instrument UID / FIGI where applicable
- source endpoint
- acquisition timestamp
- raw checksum
- transformation checksum
- parent dataset IDs
- coverage and missing-bar summary
- actual interval and timezone
- continuous/roll/adjustment semantics

## 23O note
The 23O audit treated GAZP/SBER/LKOH as file-backed research artifacts; that is scientifically weaker than a verified vendor-native identity chain and should be quarantined until provenance is upgraded.
