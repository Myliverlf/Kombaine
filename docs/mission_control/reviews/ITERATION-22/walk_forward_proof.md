# Walk-Forward Proof — Iteration 22

## Walk-Forward Semantics

### Train/Test Split
- **Train window:** Strictly BEFORE test window
- **Test window:** Strictly AFTER train window
- **No overlap:** train_end <= test_start

### No Lookahead Guarantee
- Data accessed only within designated window
- No future data used in train phase
- Results recorded with explicit train/test timestamps
- Data hashes recorded for audit trail

### Validation
WalkForwardProver validates:
1. Temporal separation (train_end <= test_start)
2. No shared data between windows
3. Data hashes are recorded

### Evidence
- Walk-forward proven for promotable candidates
- Non-promotable paths marked BLOCKED from higher maturity
- All splits recorded with timestamps and hashes

## Current Status
Walk-forward semantics are implemented and validated in core/strategy_factory.py.
The WalkForwardProver class creates and validates train/test splits.
