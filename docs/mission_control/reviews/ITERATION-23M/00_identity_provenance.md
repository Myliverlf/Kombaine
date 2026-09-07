# Iteration 23M — USDRUB Identity and Provenance

USDRUB in 23L is not a live broker instrument here. It is a **synthetic continuous file-backed research artifact**.

- canonical symbol: `USDRUB`
- UID: `file:USDRUB`
- instrument type: synthetic continuous futures-style file artifact
- market: local file-backed canonical research dataset
- source: `/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data/USDRUB_365d_1h_continuous.csv`
- candle source: local CSV resolved through canonical horizon resolver
- first timestamp: `2025-08-29T05:00:00+00:00`
- last timestamp: `2026-08-28T20:00:00+00:00`
- actual coverage: 365 days
- timezone semantics: UTC
- trading calendar: the timestamp set encoded in the local CSV; no extra calendar was synthesized

This is repository truth, not a live market identity claim.
