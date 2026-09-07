# Research Truth Gate — Iteration 23G

**RESEARCH_TRUTH_READY = NO**

Blocking reasons:
1. executable runtime risk config still differs from immutable LIVE_RISK_V1 (2.7%/2 slots versus 0.25%/1 pilot position);
2. qualification thresholds remain duplicated across legacy modules;
3. legacy data loader still has silent synthetic fallback and is not fully migrated to the canonical resolver;
4. stage gating is defined by policy but not enforced end-to-end in every consumer;
5. legacy handoff/derived-view consumers remain and require migration proof.

The horizon resolver itself is proven; the truth layer as a whole is not ready.
