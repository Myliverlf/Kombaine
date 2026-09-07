"""Iteration 01 P0: configured-universe gate for supervisor auto-swap paths.

These tests exercise the gate before the broker-facing Engine retry call.  They
use only pure/local state and never construct an Engine or Tinkoff Client.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

PROJECT = Path(__file__).resolve().parent.parent
# The live bridge resolves its canonical implementation from code/; mirror the
# service import order so this unit test exercises the same boundary.
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "code"))

from core import supervisor  # noqa: E402


UNIVERSE = ["BR", "GAZP", "LKOH", "SBER", "Si"]


def test_allowed_candidate_preserves_root_and_is_admitted() -> None:
    result = supervisor.swap_candidate_admission(
        {"ticker": "lkoh", "strategy": "volatility_squeeze"}, UNIVERSE
    )

    assert result["decision"] == "ALLOW"
    assert result["normalized_ticker"] == "LKOH"
    assert result["pipeline_stage"] == "swap_candidate"


def test_foreign_candidate_is_vetoed_before_swap_pending() -> None:
    result = supervisor.swap_candidate_admission(
        {"ticker": "IMOEX", "strategy": "vwap_bands"}, UNIVERSE
    )

    assert result["decision"] == "VETO"
    assert result["reason"] == "OUTSIDE_CONFIGURED_UNIVERSE"
    assert result["pipeline_stage"] == "swap_candidate"


@pytest.mark.parametrize("ticker", [None, "", "   ", 42, "LKU6"])
def test_unknown_candidate_fails_closed(ticker) -> None:
    result = supervisor.universe_admission(ticker, UNIVERSE)

    assert result["decision"] == "VETO"
    assert result["reason"] == "OUTSIDE_CONFIGURED_UNIVERSE"


def test_invalid_persisted_pending_is_vetoed_without_position_mutation_or_engine(monkeypatch) -> None:
    slot = {
        "ticker": "SBER",
        "open_position": {"direction": "SHORT", "qty": 1, "entry_price": 268.77},
        "swap_pending": {
            "requested_ts": 1.0,
            "target_ticker": "IMOEX",
            "target_strategy": "vwap_bands",
            "reason": "close_failed_pending_retry",
        },
    }
    original_position = copy.deepcopy(slot["open_position"])
    original_pending = copy.deepcopy(slot["swap_pending"])
    log = Mock()
    engine_factory = Mock(side_effect=AssertionError("Engine must not be constructed for invalid pending swap"))
    monkeypatch.setattr(supervisor, "log", log)
    monkeypatch.setattr(supervisor, "Engine", engine_factory)

    event = supervisor.veto_invalid_pending_swap(slot, "slot_SBER", UNIVERSE)

    assert event is not None
    assert event["decision"] == "VETO"
    assert event["reason"] == "OUTSIDE_CONFIGURED_UNIVERSE"
    assert event["original_pending"] == original_pending
    assert "swap_pending" not in slot
    assert slot["open_position"] == original_position
    engine_factory.assert_not_called()
    log.assert_called_once()
    assert "UNIVERSE_GATE" in log.call_args.args[0]
    assert "OUTSIDE_CONFIGURED_UNIVERSE" in log.call_args.args[0]


def test_valid_pending_is_unchanged_and_can_follow_existing_retry_path(monkeypatch) -> None:
    slot = {
        "ticker": "SBER",
        "open_position": {"direction": "SHORT", "qty": 1},
        "swap_pending": {
            "requested_ts": 1.0,
            "target_ticker": "LKOH",
            "target_strategy": "volatility_squeeze",
            "reason": "close_failed_pending_retry",
        },
    }
    before = copy.deepcopy(slot)
    log = Mock()
    monkeypatch.setattr(supervisor, "log", log)

    event = supervisor.veto_invalid_pending_swap(slot, "slot_SBER", UNIVERSE)

    assert event is None
    assert slot == before
    log.assert_not_called()


def test_foreign_candidate_has_no_pending_state_to_mutate() -> None:
    slot = {"ticker": "SBER", "open_position": {"direction": "SHORT", "qty": 1}}
    before = copy.deepcopy(slot)

    result = supervisor.swap_candidate_admission(
        {"ticker": "IMOEX", "strategy": "vwap_bands"}, UNIVERSE
    )

    assert result["decision"] == "VETO"
    assert slot == before
    assert "swap_pending" not in slot
