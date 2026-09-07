from core.promotion.state import PromotionState, can_transition


def test_promotion_does_not_skip_and_live_requires_chain():
    assert can_transition(PromotionState.IDEA, PromotionState.DISCOVERY)
    assert not can_transition(PromotionState.IDEA, PromotionState.BACKTESTING)
    assert can_transition(PromotionState.RESEARCH_ROBUST, PromotionState.FORWARD_ELIGIBLE)
    assert not can_transition(PromotionState.RESEARCH_ROBUST, PromotionState.LIVE_ELIGIBLE)
    assert can_transition(PromotionState.HUMAN_APPROVAL_REQUIRED, PromotionState.LIVE_ELIGIBLE)
