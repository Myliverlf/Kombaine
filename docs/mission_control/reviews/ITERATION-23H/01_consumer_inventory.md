# 01 — Consumer Inventory — Iteration 23H

**Generated:** 2026-08-30
**Scope:** Every production-reachable consumer of qualification thresholds, horizon/data loading, risk policy, stage gating, handoff, registry, reporting, scheduling

---

## Consumer Inventory

| # | Consumer | Domain | Current Source | Canonical Source | Read/Write | Production Reachable | Legacy? | Migration Required |
|---|----------|--------|---------------|-----------------|------------|---------------------|---------|-------------------|
| **QUALIFICATION_POLICY** ||||||
| Q1 | `code/qualification_campaign.py` | QUALIFICATION_POLICY | Hardcoded GATE_* constants (min_trades=8, sharpe=0.3, PF=1.05, DD=25%) | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — migrate to canonical loader** |
| Q2 | `core/research_pipeline.py:134-138` | QUALIFICATION_POLICY | Hardcoded dict (min_sharpe=0.15, PF=1.10, DD=20%, min_trades=15) | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — migrate to canonical loader** |
| Q3 | `code/candidate_gate.py:24` | QUALIFICATION_POLICY | `DEFAULT_MIN_TRADES = 30` | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — migrate to canonical loader** |
| Q4 | `code/quality_gate.py:28` | QUALIFICATION_POLICY | `DEFAULT_MIN_TRADES = 30` | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — migrate to canonical loader** |
| Q5 | `iteration12_proof.py:77` | QUALIFICATION_POLICY | `MIN_TRADES = 5` | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — migrate to canonical loader** |
| Q6 | `code/coverage_repair_generator.py` | QUALIFICATION_POLICY | Parameter-based (min_trades, min_pf) | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — pass canonical values** |
| Q7 | `core/market_regime.py:1281` | QUALIFICATION_POLICY | `MIN_TRADES_FOR_EVIDENCE = 5` | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — migrate to canonical loader** |
| Q8 | `tests/test_canonical_truth_contracts_23g.py` | QUALIFICATION_POLICY | Direct JSON read | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | NO (test) | NO | Verify against canonical |
| Q9 | `tests/test_research_truth_23g.py` | QUALIFICATION_POLICY | Direct JSON read | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | NO (test) | NO | Verify against canonical |
| **HORIZON_DATA** ||||||
| H1 | `code/data_loader.py` (load_ohlcv_horizon) | HORIZON_DATA | Own `_candidate_csv_paths()` with hardcoded 60/365/1095 fallback | `core/horizon_resolution.py` `resolve_horizon()` | read/write | YES | NO | **YES — migrate to canonical resolver** |
| H2 | `core/engine.py:173` | HORIZON_DATA | `load_ohlcv()` from data_loader | `core/horizon_resolution.py` via data_loader adapter | read | YES | NO | **YES — data_loader migration covers this** |
| H3 | `code/risk_scorecard.py:153` | HORIZON_DATA | `load_ohlcv()` from data_loader | `core/horizon_resolution.py` via data_loader adapter | read | YES | NO | **YES — data_loader migration covers this** |
| H4 | `code/scorecard_dryrun_verdict.py:28` | HORIZON_DATA | `load_ohlcv()` from data_loader | `core/horizon_resolution.py` via data_loader adapter | read | YES | NO | **YES — data_loader migration covers this** |
| H5 | `code/e2e_data_loader_dryrun.py:51` | HORIZON_DATA | `load_ohlcv()`, `load_universe()` | `core/horizon_resolution.py` via data_loader adapter | read | YES | NO | **YES — data_loader migration covers this** |
| H6 | `code/strategy_architect_autopilot.py:523` | HORIZON_DATA | References `data_loader.py` in module_paths | `core/horizon_resolution.py` via data_loader adapter | read | YES | NO | **YES — data_loader migration covers this** |
| **RISK_POLICY** ||||||
| R1 | `core/config.py` | RISK_POLICY | `config.json` (risk_per_trade_pct=2.7, go_budget_pct=50, max_slots=2) | `state/live_risk/LIVE_RISK_V1.json` via `core/risk_policy_loader.py` | read/write | YES | YES (differs from LIVE_RISK_V1) | **YES — align executable checks** |
| R2 | `core/seeder.py:193` | RISK_POLICY | Hardcoded `go_budget = deposit * 0.40 * 0.70` | `state/live_risk/LIVE_RISK_V1.json` via `core/risk_policy_loader.py` | read | YES | YES | **YES — align to canonical** |
| R3 | `core/risk.py` | RISK_POLICY | Uses `cfg.go_budget_rub` from config.json | `state/live_risk/LIVE_RISK_V1.json` via `core/risk_policy_loader.py` | read | YES | YES | **YES — qualification-time must use LIVE_RISK_V1** |
| R4 | `core/engine.py:349` | RISK_POLICY | Uses `cfg.risk_per_trade_rub` from config.json | `state/live_risk/LIVE_RISK_V1.json` via `core/risk_policy_loader.py` | read | YES | YES | **YES — align** |
| R5 | `core/system_certification.py:1197` | RISK_POLICY | Checks `risk_per_trade_pct <= 5` | `state/live_risk/LIVE_RISK_V1.json` via `core/risk_policy_loader.py` | read | YES | YES | **YES — align to 0.25%** |
| R6 | `tests/test_live_preconditions_23b.py` | RISK_POLICY | Reads LIVE_RISK_V1.json directly | `core/risk_policy_loader.py` | read | NO (test) | NO | Verify against canonical |
| **STAGE_GATING** ||||||
| S1 | `core/research_handoff.py` | STAGE_GATING | Checks `qualification_stage == "PAPER_ADMISSION_READY"` | `core/stage_machine.py` via `core/canonical_policy_loader.py` | read/write | YES | NO | **YES — migrate to stage machine** |
| S2 | `core/strategy_factory.py:61` | STAGE_GATING | `HypothesisStatus` enum (WALK_FORWARD etc.) | `core/stage_machine.py` | read | YES | NO | **YES — align enum to canonical stages** |
| S3 | `core/strategy_lifecycle.py:117` | STAGE_GATING | `EvidenceClass.WALK_FORWARD` | `core/stage_machine.py` | read | YES | NO | **YES — align** |
| S4 | `core/performance_attribution.py:36` | STAGE_GATING | `WALK_FORWARD` string | `core/stage_machine.py` | read | YES | NO | **YES — align** |
| S5 | `core/mission_control.py:128` | STAGE_GATING | `WALK_FORWARD_REVALIDATION` | `core/stage_machine.py` | read | YES | NO | **YES — align** |
| S6 | `tests/test_canonical_truth_contracts_23g.py` | STAGE_GATING | Reads stages from JSON | `core/stage_machine.py` | read | NO (test) | NO | Verify |
| S7 | `tests/test_research_truth_23g.py` | STAGE_GATING | Reads stages from JSON | `core/stage_machine.py` | read | NO (test) | NO | Verify |
| **RESEARCH_HANDOFF** ||||||
| HH1 | `core/seeder_handoff.py` | RESEARCH_HANDOFF | Canonical eligible_candidates.json + legacy scan fallback | `core/eligible_candidates_contract.py` | read/write | YES | NO (canonical default) | Verify canonical path active |
| HH2 | `core/seeder.py:83` | RESEARCH_HANDOFF | `SCAN_RESULTS` hardcoded legacy path | `core/eligible_candidates_contract.py` | read | YES | YES (legacy) | **YES — ensure legacy path blocked when canonical available** |
| HH3 | `code/seed_pool.py:19` | RESEARCH_HANDOFF | Hardcoded scan_results path | `core/eligible_candidates_contract.py` | read | YES | YES (legacy) | **YES — block or redirect** |
| HH4 | `core/research_handoff.py` | RESEARCH_HANDOFF | Canonical handoff validation | `core/eligible_candidates_contract.py` | read/write | YES | NO | Verify |
| **REGISTRY** ||||||
| RG1 | `core/strategy_registry.py` | REGISTRY | `state/strategy_registry.json` (canonical SOT) | `state/strategy_registry.json` via `core/strategy_registry.py` | read/write | YES | NO | Verify sole-writer enforced |
| RG2 | `core/registry.py` | REGISTRY | Loads `code/strategy_registry.py` via importlib | `core/strategy_registry.py` | read/write | YES | NO | Verify no legacy write paths |
| RG3 | `core/seeder.py:237` | REGISTRY | `load_registry()` | `core/strategy_registry.py` | read/write | YES | NO | Verify |
| **DERIVED_VIEWS** ||||||
| DV1 | `state/signal_pool.json` | DERIVED_VIEWS | Projection from registry | Must remain read-only projection | read | YES | NO | Verify write-back blocked |
| DV2 | `state/waitlist.json` | DERIVED_VIEWS | Projection from registry | Must remain read-only projection | read | YES | NO | Verify write-back blocked |
| DV3 | `code/audit_signal_pool.py` | DERIVED_VIEWS | Reads signal_pool, waitlist | Read-only | read | YES | NO | Verify |
| DV4 | `code/pool_governance.py` | DERIVED_VIEWS | Reads signal_pool | Read-only | read | YES | NO | Verify |
| **REPORTING** ||||||
| RP1 | `code/strategy_architect_autopilot.py` | REPORTING | Uses hardcoded thresholds for reports | `config/research_qualification_policy.json` via `core/canonical_policy_loader.py` | read | YES | NO | **YES — use canonical policy for reports** |
| RP2 | `code/daily_morning_report.py` | REPORTING | Reads signal_pool | Canonical sources | read | YES | NO | Verify |
| RP3 | `code/scorecard_report.py` | REPORTING | Uses various sources | Canonical sources | read | YES | NO | Verify |
| **SCHEDULER** ||||||
| SC1 | `combine-research-daily.timer` | SCHEDULER | Active systemd timer | Canonical owner: `core/run_contract.py` | — | YES | NO | Verify legacy path blocked |
| SC2 | `combine-seeder.timer` | SCHEDULER | Active systemd timer | Canonical owner: `core/strategy_registry.py` | — | YES | NO | Verify |
| SC3 | `combine-supervisor.timer` | SCHEDULER | Active systemd timer | Canonical owner: `core/supervisor.py` | — | YES | NO | Verify |
| SC4 | `combine-15m.timer` | SCHEDULER | Active systemd timer | Data download | — | YES | NO | Verify |

---

## Summary

| Domain | Total Consumers | Migration Required | Already Canonical | Legacy Unreachable |
|--------|----------------|-------------------|-------------------|-------------------|
| QUALIFICATION_POLICY | 9 | 7 | 2 (tests) | 0 |
| HORIZON_DATA | 6 | 6 | 0 | 0 |
| RISK_POLICY | 6 | 5 | 1 (test) | 0 |
| STAGE_GATING | 7 | 5 | 2 (tests) | 0 |
| RESEARCH_HANDOFF | 4 | 2 | 2 | 0 |
| REGISTRY | 3 | 0 | 3 | 0 |
| DERIVED_VIEWS | 4 | 0 | 4 | 0 |
| REPORTING | 3 | 1 | 2 | 0 |
| SCHEDULER | 4 | 0 | 4 | 0 |
| **TOTAL** | **46** | **26** | **20** | **0** |

---

## Key Findings

1. **QUALIFICATION_POLICY**: 5 modules have hardcoded thresholds that DIFFER from canonical policy. qualification_campaign.py matches canonical (min_trades=8, sharpe=0.3, PF=1.05, DD=25%). research_pipeline.py is WEAKEST (min_trades=15, sharpe=0.15, PF=1.10). candidate_gate.py and quality_gate.py are STRONGEST (min_trades=30).

2. **HORIZON_DATA**: data_loader.py has its own `_candidate_csv_paths()` that bypasses the canonical `resolve_horizon()`. All 5 downstream consumers inherit this bypass.

3. **RISK_POLICY**: config.json (risk_per_trade_pct=2.7, go_budget_pct=50, max_slots=2) differs FUNDAMENTALLY from LIVE_RISK_V1 (0.25%, 10%, 1 position). This is the highest-severity drift.

4. **STAGE_GATING**: Stage names exist in multiple enums across modules but no programmatic enforcement machine exists.

5. **RESEARCH_HANDOFF**: Seeder has a hardcoded legacy SCAN_RESULTS path. seeder_handoff.py correctly gates legacy behind `use_legacy=True`, but seeder.py line 83 still references the old path.
