# Failure Matrix — Iteration 15

| ID | Description | Test | Result |
|----|-------------|------|--------|
| F1 | Regime DB unavailable | TestF1DBUnavailable | PASS — graceful handling |
| F2 | Source market data missing | TestF2MissingData | PASS — returns None |
| F3 | Stale market data | TestF3StaleData | PASS — detected as STALE |
| F4 | Insufficient warm-up | TestF4InsufficientWarmup | PASS — INSUFFICIENT confidence |
| F5 | Future leakage | TestF5FutureLeakage | PASS — no lookahead |
| F6 | Threshold policy missing | TestF6ThresholdMissing | PASS — defaults available |
| F7 | Policy version mismatch | TestF7PolicyVersionMismatch | PASS — versions recorded |
| F8 | Feature NaN | TestF8FeatureNaN | PASS — confidence downgrade |
| F9 | Extreme label flicker | TestF9LabelFlicker | PASS — transitions countable |
| F10 | All-one-regime collapse | TestF10AllOneRegime | PASS — distribution reported |
| F11 | Research dataset mismatch | TestF11DatasetMismatch | PASS — build IDs separate |
| F12 | Attribution timestamp mismatch | TestF12TimestampMismatch | PASS — outside range = INSUFFICIENT |
| F13 | Weak attribution + high regime confidence | TestF13WeakAttributionHighRegime | PASS — preserved |
| F14 | Exact attribution + insufficient regime confidence | TestF14ExactAttributionInsufficientRegime | PASS — INSUFFICIENT |
| F15 | Trade spans regime transition | TestF15TradeSpansTransition | PASS — both regimes recorded |
| F16 | Multi-timeframe conflict | TestF16MultiTimeframeConflict | PASS — stored independently |
| F17 | Sparse regime sample | TestF17SparseSample | PASS — INSUFFICIENT maturity |
| F18 | Contradictory research/paper evidence | TestF18ContradictoryEvidence | PASS — both preserved |
| F19 | Fixture evidence leaks to production | TestF19FixtureLeakage | PASS — build IDs separate |
| F20 | Regime layer attempts registry mutation | TestF20RegistryMutationAttempt | PASS — no mutation code |
| F21 | Regime layer attempts broker mutation | TestF21BrokerMutationAttempt | PASS — no broker code |
| F22 | Lifecycle auto-rotation attempted | TestF22AutoRotation | PASS — no rotation code |
| F23 | Stale current snapshot | TestF23StaleSnapshot | PASS — detected as STALE |
| F24 | Duplicate build | TestF24DuplicateBuild | PASS — idempotent |

## Result: ALL 24 FAILURE MODES TESTED AND PASSING
