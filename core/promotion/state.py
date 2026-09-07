from __future__ import annotations

from enum import Enum


class PromotionState(str, Enum):
    IDEA = 'IDEA'
    DISCOVERY = 'DISCOVERY'
    DEVELOPMENT = 'DEVELOPMENT'
    PREREGISTERED = 'PREREGISTERED'
    BACKTESTING = 'BACKTESTING'
    VALIDATION_FAILED = 'VALIDATION_FAILED'
    VALIDATION_PASSED = 'VALIDATION_PASSED'
    BEHAVIORAL_DUPLICATE = 'BEHAVIORAL_DUPLICATE'
    RESEARCH_ROBUST = 'RESEARCH_ROBUST'
    FORWARD_ELIGIBLE = 'FORWARD_ELIGIBLE'
    FORWARD_ACTIVE = 'FORWARD_ACTIVE'
    FORWARD_FAILED = 'FORWARD_FAILED'
    RISK_REVIEW = 'RISK_REVIEW'
    HUMAN_APPROVAL_REQUIRED = 'HUMAN_APPROVAL_REQUIRED'
    LIVE_ELIGIBLE = 'LIVE_ELIGIBLE'
    LIVE_ACTIVE = 'LIVE_ACTIVE'
    PAUSED = 'PAUSED'
    RETIRED = 'RETIRED'


_ALLOWED = {
    PromotionState.IDEA: {PromotionState.DISCOVERY},
    PromotionState.DISCOVERY: {PromotionState.DEVELOPMENT, PromotionState.PREREGISTERED},
    PromotionState.DEVELOPMENT: {PromotionState.PREREGISTERED, PromotionState.RESEARCH_ROBUST},
    PromotionState.PREREGISTERED: {PromotionState.BACKTESTING},
    PromotionState.BACKTESTING: {PromotionState.VALIDATION_PASSED, PromotionState.VALIDATION_FAILED},
    PromotionState.VALIDATION_PASSED: {PromotionState.RESEARCH_ROBUST, PromotionState.BEHAVIORAL_DUPLICATE},
    PromotionState.RESEARCH_ROBUST: {PromotionState.FORWARD_ELIGIBLE},
    PromotionState.FORWARD_ELIGIBLE: {PromotionState.FORWARD_ACTIVE, PromotionState.RISK_REVIEW},
    PromotionState.RISK_REVIEW: {PromotionState.HUMAN_APPROVAL_REQUIRED, PromotionState.FORWARD_FAILED},
    PromotionState.HUMAN_APPROVAL_REQUIRED: {PromotionState.LIVE_ELIGIBLE},
    PromotionState.LIVE_ELIGIBLE: {PromotionState.LIVE_ACTIVE},
}


def can_transition(src: PromotionState, dst: PromotionState) -> bool:
    return dst in _ALLOWED.get(src, set())
