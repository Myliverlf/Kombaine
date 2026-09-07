from core.evidence.behavioral import BehavioralEvidenceCluster, EvidenceClusterRegistry
from core.candidates.registry import CandidateRecord, CandidateRegistry
from core.research.evidence_gate import EvidenceAuthorization
from core.promotion.state import PromotionState, can_transition


def test_behavioral_clusters_count_independent_evidence():
    reg = EvidenceClusterRegistry(schema_version='1.0.0', registry_id='ev-reg', created_at='2026-08-31T00:00:00+00:00')
    c1 = reg.assign('cand-1', features={'ret': 0.9})
    c2 = reg.assign('cand-2', features={'ret': 0.91})
    assert reg.raw_pass_count == 2
    assert reg.independent_cluster_count == 2 or reg.independent_cluster_count == 1
    assert c1.representative_candidate_id in c1.member_candidate_ids
    assert c2.representative_candidate_id in c2.member_candidate_ids


def test_candidate_registry_blocks_unauthorized_writes():
    reg = CandidateRegistry(schema_version='1.0.0', registry_id='cand-reg', created_at='2026-08-31T00:00:00+00:00')
    record = CandidateRecord(
        candidate_id='cand-1', experiment_id='exp-1', campaign_id='camp-1', strategy_id='strat-1', strategy_version='1.0.0', parameters={}, dataset_ids=['ds-1'], instrument_ids=['uid-gazp'], policy_version='1.0.0', cost_model_version='1.0.0', result_hashes=['h'], ingress_receipt_ids=['ing-1'], evidence_authorization_ids=['auth-1'], behavioral_cluster_id='cluster-1', scientific_validity='VALID', qualification_status='QUALIFIED', quarantine_status='NONE', forward_eligibility='NO', promotion_state='DISCOVERY', human_approval_state='NONE', timestamps={'created_at': '2026-08-31T00:00:00+00:00'}
    )
    auth = EvidenceAuthorization(False, 'BLOCKED_NO_CONTEXT', 'missing VerifiedResearchContext')
    try:
        reg.register(record, evidence_auth=auth)
    except Exception as exc:
        assert 'CANONICAL_WRITE_BLOCKED' in str(exc)
    else:
        raise AssertionError('expected gate block')


def test_promotion_human_approval_and_live_chain():
    assert can_transition(PromotionState.BACKTESTING, PromotionState.VALIDATION_PASSED)
    assert can_transition(PromotionState.VALIDATION_PASSED, PromotionState.RESEARCH_ROBUST)
    assert can_transition(PromotionState.RESEARCH_ROBUST, PromotionState.FORWARD_ELIGIBLE)
    assert can_transition(PromotionState.FORWARD_ELIGIBLE, PromotionState.FORWARD_ACTIVE)
    assert can_transition(PromotionState.FORWARD_ACTIVE, PromotionState.LIVE_ELIGIBLE) is False
    assert can_transition(PromotionState.HUMAN_APPROVAL_REQUIRED, PromotionState.LIVE_ELIGIBLE)
