import json
from pathlib import Path


def test_23o_evidence_status_file_reclassifies_science():
    status = json.loads(Path('/root/prop-desk/strategy_combine/state/23o_evidence_status.json').read_text())
    assert status['process_validity'] == 'VALID'
    assert status['scientific_equity_validity'] == 'INVALID_FOR_EQUITY'


def test_23o_quarantined_datasets_recorded():
    reg = json.loads(Path('/root/prop-desk/strategy_combine/state/dataset_registry.json').read_text())
    quarantined = [k for k, v in reg['datasets'].items() if v['provenance_status'] == 'QUARANTINED']
    assert '23o_gazp_quarantined' in quarantined
    assert '23o_sber_quarantined' in quarantined
    assert '23o_lkoh_quarantined' in quarantined
