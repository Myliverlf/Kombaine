# Stop/Kill Procedure — Iteration 22

## Full Stop Procedure
1. **STOP SIGNAL GENERATION** — Disable signal pool rotation, stop signal_fusion from emitting new intents
2. **STOP STRATEGY ACTIVATION** — Set research pipeline to PAUSED, block seeder_handoff from promoting new candidates
3. **PAUSE RESEARCH** — Disable combine-research-daily.timer, wait for current cycle to complete
4. **PRESERVE VISIBILITY** — Keep production_truth, performance_attribution, system_health active
5. **ESCALATE TO HUMAN** — Enumerate all open positions, generate report, send Telegram notification

## Critical Invariant
- **NO AUTO-LIQUIDATION** — Closing positions requires separate human authorization
- Stop procedure NEVER implements emergency liquidation orders
- Human operator decides HOLD, CLOSE, or MODIFY each position

## Emergency Contact
Telegram notification (when configured) for human escalation.
