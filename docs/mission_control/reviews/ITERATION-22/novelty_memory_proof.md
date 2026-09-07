# Novelty & Experiment Memory Proof — Iteration 22

## Experiment Memory State
- **Families indexed:** 340
- **Instances indexed:** 460
- **Indexed runs:** 3
- **Schema version:** 1.0.0
- **DB location:** state/experiment_memory.db

## Classification Categories
| Classification | Action | Description |
|---------------|--------|-------------|
| NEW | RUN | Genuinely new experiment |
| EXACT_DUPLICATE | SKIP | Same instance already tested |
| REVALIDATION | RUN | Re-test known evidence |
| METHODOLOGY_CHANGE | RUN | Different validation protocol |
| CODE_CHANGE | RUN | Different code version |
| COST_MODEL_CHANGE | RUN | Different cost assumptions |
| INCOMPARABLE | RUN | Cannot compare to prior |

## Duplicate Prevention Chain
1. Plan generation creates candidates
2. ExperimentMemory.classify_candidate() checks each against history
3. NoveltyGate applies policy: only EXACT_DUPLICATE → SKIP
4. Forced reproduction available via --force-reproduction
5. All decisions logged to novelty_decisions.jsonl

## Evidence Retention
- All experiment results persist in experiment_memory.db
- Rejected candidates remain in memory
- No evidence inflation from rerunning same config
- Classification is observation-only (does not affect execution)
