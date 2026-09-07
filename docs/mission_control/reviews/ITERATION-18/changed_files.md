# Changed Files — Iteration 18

## New Files
| File | Purpose |
|------|---------|
| core/mission_control.py | Mission Control orchestrator (1660 lines) |
| tests/test_mission_control.py | 70 tests (T1-T24, F1-F24, integration) |
| docs/mission_control/reviews/ITERATION-18/ | Evidence bundle |

## Modified Files
| File | Change |
|------|--------|
| docs/mission_control/01_SYSTEM_MAP.md | Added Mission Control layer |
| docs/mission_control/02_SOURCE_OF_TRUTH.md | Added MC state store |
| docs/mission_control/08_TECH_DEBT_REGISTER.md | Added MC-related items |
| docs/mission_control/09_ROADMAP.md | Updated with Iteration 18 |
| docs/mission_control/10_MATURITY_MODEL.md | Updated maturity level |

## Unchanged Files
All existing core modules (system_health, research_pipeline, strategy_lifecycle,
performance_attribution, market_regime, replacement_ranking, human_review) remain
unchanged. MC reads from them but does not modify them.
