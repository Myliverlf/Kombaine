# Contradiction Policy — Iteration 09

## Principle

A contradiction is knowledge, not an error to hide.

## Detection

When observations in the same comparable group have:
- positive metric direction AND negative metric direction
- with contradiction_ratio >= 0.3

## Response

1. Finding type: CONTRADICTION
2. Finding status: CONTESTED
3. Confidence: reduced by one level
4. Evidence: both supporting AND contradicting observations tracked
5. Open question: generated with reason CONTRADICTORY_RESULTS

## What Does NOT Happen

- Old conclusion is NOT overwritten
- Finding is NOT retracted
- Trading is NOT affected
- Research is NOT autonomously scheduled

## History

Material finding changes are recorded in finding_history table.
