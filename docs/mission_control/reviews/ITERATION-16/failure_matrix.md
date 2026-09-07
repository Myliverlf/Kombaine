# Failure Matrix — Iteration 16

**Date:** 2026-08-30

| ID | Scenario | Handling |
|---|---|---|
| F1 | Ranking DB unavailable | build_ranking works without store; corrupt DB renamed |
| F2 | No active strategies | Empty incumbent list, no comparisons |
| F3 | No candidates | NO_VALID_CANDIDATE for all incumbents |
| F4 | Stale registry | Hash captured, age reported in health check |
| F5 | Missing performance evidence | Reduced scores, lower confidence |
| F6 | Missing regime evidence | Neutral regime_score (0.3) |
| F7 | Lifecycle unavailable | Unknown lifecycle, neutral score |
| F8 | Contradictory evidence | Confidence reduced, reason codes added |
| F9 | Tiny candidate sample | Low confidence, maturity = INSUFFICIENT/EARLY |
| F10 | Candidate duplicates incumbent | SAME_FAMILY_DUPLICATE hard gate |
| F11 | Candidate highly correlated | High correlation penalty in diversification |
| F12 | Concentration worsens | Risk penalty from drawdown + concentration |
| F13 | Regime favorable but history weak | Regime bonus small, total score low |
| F14 | Incumbent decay confirmed | Lower replacement barrier |
| F15 | Healthy mature incumbent | Higher barrier, KEEP/WATCH expected |
| F16 | Equal candidates / tie | Small margin, no false certainty |
| F17 | Missing broker portfolio | Zero exposure, snapshot valid |
| F18 | Analytics mismatch | Recorded, not reconciled |
| F19 | Repeated experiments inflate evidence | Same config → same score, no inflation |
| F20 | Ranking tries registry mutation | Zero registry writes |
| F21 | Ranking tries swap mutation | Zero swap field creation |
| F22 | Ranking tries broker mutation | Zero broker API calls |
| F23 | Policy version mismatch | Version recorded per build |
| F24 | Fixture evidence leaks | Test isolation via tmp_path |
