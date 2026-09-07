# Small Multi-Horizon Proof — Iteration 23G

Resolver proof executed with `PYTHONPATH=/root/prop-desk/futures_lab:/root/prop-desk/strategy_combine`.

- GAZP/SBER × 15m/1h: 60d exact resolution PASS.
- GAZP/SBER × 15m/1h: 90d deterministic slice from 365d PASS.
- GAZP/SBER × 15m/1h: 180d deterministic slice from 365d PASS.
- GAZP/SBER × 15m/1h: 365d exact resolution PASS.
- GAZP/SBER × 15m/1h: 1095d FAIL CLOSED because nominal 1095d files have actual coverage below 1095d.

No candidate activation and no broker calls.
