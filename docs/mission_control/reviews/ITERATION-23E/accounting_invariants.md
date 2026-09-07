# Accounting Invariants — Iteration 23E

`planned = executed + skipped + not_yet_started`
`executed = completed_valid + rejected_by_evidence + infrastructure_error + data_error + parameter_error + invalidated`

Observed: `planned=12`, `executed=10`, `skipped=1`, `not_yet_started=1`, `accounting_ok=true`.
