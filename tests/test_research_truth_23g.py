from pathlib import Path
import json
import pytest
ROOT=Path(__file__).resolve().parents[1]

def test_canonical_truth_manifest():
    m=json.loads((ROOT/'docs/mission_control/canonical_truth.json').read_text())
    assert m['canonical_architecture']=='docs/COMBINE_SYSTEM_ARCHITECTURE.md'
    assert m['canonical_qualification_policy']=='config/research_qualification_policy.json'
    assert m['canonical_registry']=='state/strategy_registry.json'

def test_policy_stage_semantics():
    p=json.loads((ROOT/'config/research_qualification_policy.json').read_text())
    assert p['stages']['DISCOVERY']['paper_admission'] is False
    assert p['stages']['PAPER_ADMISSION_READY']['paper_admission'] is True
    assert 'MULTI_HORIZON_QUALIFIED' in p['stages']['PAPER_ADMISSION_READY']['requires']
    assert '60d_only_cannot_reach_PAPER_ADMISSION_READY' in p['invariants']

def test_handoff_schema_and_immutability(tmp_path):
    from core.research_handoff import write_handoff
    payload={'run_id':'r1','candidate_id':'c1','family_id':'f1','instrument':'GAZP','timeframe':'1h','params':{'x':1},'qualification_stage':'BACKTEST_QUALIFIED','policy_version':'1.0.0','policy_hash':'h','data_hashes':{'60d':'d'},'cost_model_hash':'c','risk_status':'NOT_QUALIFIED','provenance':{}}
    p=write_handoff(tmp_path,'r1',payload)
    assert p.exists()
    assert write_handoff(tmp_path,'r1',payload)==p
    payload['params']={'x':2}
    with pytest.raises(FileExistsError): write_handoff(tmp_path,'r1',payload)
