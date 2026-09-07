# Confidence Model — Iteration 09

## Levels

| Level | Meaning |
|---|---|
| INSUFFICIENT | < 2 comparable observations; cannot draw conclusion |
| LOW | 2-3 observations; low consistency or few trades |
| MEDIUM | 4+ observations; moderate consistency; some trades |
| HIGH | 6+ observations; high consistency; many trades; multiple revalidations |

## Dimensions

- evidence_count: unique observations
- independent_revalidations: REVALIDATION-classified observations
- unique_instruments: breadth of evidence
- consistency_ratio: fraction with consistent direction
- total_trades: sample size
- contradiction_count: opposing observations
- methodology_versions: validation approach diversity

## Adjustments

- Contradictions reduce confidence (HIGH→MEDIUM, MEDIUM→LOW)
- Few revalidations cap at MEDIUM
- Missing metrics tracked but not converted to zero

## Basis

Every confidence value has explicit confidence_basis text explaining WHY.
No arbitrary AI confidence percentages.
