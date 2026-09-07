# Ranking Contract — Iteration 16

**Date:** 2026-08-30
**Module:** core/replacement_ranking.py
**Store:** state/replacement_ranking.db

## Input Contract
- registry_data: Dict[str, Any] — strategy registry JSON
- lifecycle_data: Optional[Dict] — lifecycle observer output per strategy
- regime_data_map: Optional[Dict] — regime evidence per strategy
- current_regime: Optional[Dict] — current market regime classification
- open_positions: Optional[List[Dict]] — broker-reported open positions
- max_slots: int — portfolio slot capacity
- capital: float — available capital
- policy: RankingPolicy — versioned scoring policy

## Output Contract
- RankingBuild: metadata (build_id, counts, hashes, status)
- ReplacementComparison: per-pair (decision, scores, reason codes, explanation)
- RankingExplanation: per-incumbent (decision, urgency, what supports/contradicts)
- CandidateScore: per-strategy (multidimensional score, maturity, confidence)

## Decision Enum
KEEP | WATCH | REVALIDATE | REPLACEMENT_CANDIDATE | INSUFFICIENT_EVIDENCE | NO_VALID_CANDIDATE

## Safety
- Zero broker-mutating calls
- Zero registry mutations
- Zero swap mutations
- Advisory only
