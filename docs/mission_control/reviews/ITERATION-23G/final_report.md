# Iteration 23G — Final Report

## A. Executive result
RESEARCH_INFRASTRUCTURE_BLOCKED

## B. Canonical truth map
- architecture: `docs/COMBINE_SYSTEM_ARCHITECTURE.md`
- qualification policy: `config/research_qualification_policy.json`
- risk policy: `state/live_risk/LIVE_RISK_V1.json`
- horizon resolver: `core/horizon_resolution.py`
- research handoff: `reports/strategy_architect/runs/{run_id}/eligible_candidates.json`
- registry: `state/strategy_registry.json`
- scheduler: `combine-research-daily.timer` → `combine-research-daily.service`

## C. Architecture consolidation
The canonical architecture is `docs/COMBINE_SYSTEM_ARCHITECTURE.md`. Other architecture documents are historical/non-canonical and are explicitly marked in the machine-readable truth manifest.

## D. Qualification consolidation
One machine-readable policy now exists, but not all legacy consumers have migrated; duplicated thresholds remain a blocking contradiction.

## E. Horizon behavior
60d: exact 60d preferred. 90d/180d: deterministic tail slices from certified 365d. 365d: exact 365d preferred. 1095d: exact source only and fail-closed when actual coverage is below 1095d.

## F. 1095 mismatch
GAZP/SBER nominal 1095d files contain approximately 696 days actual coverage. The resolver rejects them for 1095d instead of trusting the filename.

## G. Data certification
GAZP/SBER at 15m/1h: 60/90/180/365 resolution proof PASS; 1095 FAIL CLOSED.

## H. data_loader repair
New canonical resolver and tests exist. Full migration of legacy `code/data_loader.py` callers is not complete; synthetic fallback remains a qualification-integrity risk.

## I. Risk alignment
`LIVE_RISK_V1` is canonical. Executable `config.json` remains materially different; this is an explicit PRE-LIVE blocker, not silently changed in 23G.

## J. Handoff
Immutable run-specific `eligible_candidates.json` contract exists via `core/eligible_candidates_contract.py` and `core/run_contract.py`. Full consumer migration remains to be proven.

## K. Registry ownership
Registry is canonical writable strategy state; waitlist/signal_pool are derived compatibility views. Legacy writers/consumers remain a migration debt.

## L. Scheduler truth
`combine-research-daily.timer` is active/enabled and is the canonical research owner. Manual-only historical wording is stale.

## M. Reporting truth
The new resolver exposes requested horizon, source label, actual coverage, slice metadata and file hash. Reports must consume this metadata; legacy reports remain historical.

## N. Contradiction matrix
30 tracked items. Closed/clarified: canonical architecture metadata, scheduler ownership, horizon fail-closed behavior, handoff contract. Blocking/accepted debt: risk drift, threshold duplication, loader migration, stage enforcement and legacy consumers.

## O. Small proof
GAZP/SBER × 15m/1h: 60/90/180/365 resolve; 1095 fails closed.

## P. Tests
Focused: 160 passed. Full regression: 2294 passed, 1 skipped, 2 warnings.

## Q. Safety
real orders created: NO
real orders cancelled: NO
real positions changed: NO
external LKOH changed: NO
broker-mutating calls: NO
risk thresholds weakened: NO
eligibility thresholds weakened: NO
cost model weakened: NO
human LIVE authorization issued: NO
LIVE_EXECUTE enabled: NO
mode changed: NO
paper_first changed: NO
LIVE activated: NO

## R. Research truth gate
RESEARCH_TRUTH_READY = NO

## S. 23F resume permission
23F_FULL_CAMPAIGN_ALLOWED = NO

## T. Remaining blockers
- LIVE_RISK_V1 vs executable risk config drift
- duplicated qualification thresholds
- incomplete data_loader migration and silent synthetic fallback
- incomplete programmatic stage enforcement
- legacy handoff/derived-view consumer migration
- insufficient actual 1095d history for GAZP/SBER

## U. Exactly ONE next bounded task
Align all qualification consumers and executable risk checks to the canonical machine-readable policy, without enabling LIVE, then rerun the 23G cross-layer contract suite.
