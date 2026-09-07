def test_provider_identity_collision_proof():
    from pathlib import Path
    import json
    proof = json.loads(Path('/root/prop-desk/strategy_combine/reports/equity_identity_proof_24h.json').read_text())
    assert proof['GAZP']['uid'] != proof['GAZPF']['uid']
    assert proof['SBER']['uid'] != proof['SBERF']['uid']
    assert proof['GAZP']['class_code'] == 'TQBR'
    assert proof['GAZPF']['class_code'] == 'SPBFUT'
