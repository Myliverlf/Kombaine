#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from live_safe_dashboard_data import DEFAULT_DRY_RUN_DIR, DEFAULT_STATE_DIR, load_dashboard_bundle
from live_safe_dashboard_metrics import build_allocator_scorecard, build_metrics_summary
from live_safe_dashboard_render import render_markdown
from live_safe_dashboard_scorecard import build_dashboard_from_fixture
from pipeline_map import MAX_CONTRACTS_PER_ENTRY, MAX_LIVE_SLOTS, EXCLUDED_TICKERS


@pytest.fixture(scope="module")
def dashboard_bundle() -> dict:
    return load_dashboard_bundle(DEFAULT_DRY_RUN_DIR, DEFAULT_STATE_DIR)


@pytest.fixture(scope="module")
def dashboard_payload(dashboard_bundle: dict) -> dict:
    return build_dashboard_from_fixture(DEFAULT_DRY_RUN_DIR, DEFAULT_STATE_DIR)


@pytest.fixture(scope="module")
def metrics_payload(dashboard_bundle: dict) -> dict:
    return build_metrics_summary(dashboard_bundle["strategy_registry"], dashboard_bundle["portfolio"])


def test_scorecard_payload_contains_requested_kpis(dashboard_payload: dict) -> None:
    metrics = dashboard_payload["metrics"]
    counts = metrics["counts"]
    perf = metrics["performance"]
    decisions = metrics["decisions"]

    assert dashboard_payload["pipeline"]["live_orders_allowed"] is False
    assert dashboard_payload["pipeline"]["max_live_slots"] == MAX_LIVE_SLOTS
    assert dashboard_payload["pipeline"]["max_contracts_per_entry"] == MAX_CONTRACTS_PER_ENTRY
    assert dashboard_payload["pipeline"]["ri_excluded"] is True
    assert "RI" in EXCLUDED_TICKERS
    assert counts["assets"] >= 1
    assert counts["strategies"] >= 1
    assert counts["trades"] >= 1
    assert perf["PF"] >= 0.0
    assert perf["DD"] >= 0.0
    assert decisions["keep"] + decisions["drop"] + decisions["retest"] >= 1


def test_metrics_summary_has_pnl_and_risk_direction(metrics_payload: dict) -> None:
    allocator = build_allocator_scorecard(metrics_payload, {
        "max_live_slots": MAX_LIVE_SLOTS,
        "max_contracts_per_entry": MAX_CONTRACTS_PER_ENTRY,
    })

    assert metrics_payload["performance"]["PnL"] >= 0.0
    assert "risk_density" in metrics_payload["risk"]
    assert allocator["limits"]["slots"] <= MAX_LIVE_SLOTS
    assert allocator["limits"]["max_contracts_per_entry"] == MAX_CONTRACTS_PER_ENTRY
    assert 0.0 <= allocator["composite_score"] <= 1.0
    assert isinstance(allocator["direction"]["pnl_up"], bool)
    assert isinstance(allocator["direction"]["risk_down"], bool)


def test_render_includes_kpi_cards_and_guardrails(dashboard_payload: dict) -> None:
    text = render_markdown(dashboard_payload)
    required_phrases = [
        "assets:",
        "strategies:",
        "trades:",
        "win/loss:",
        "PnL/PF/DD:",
        "decision",
        "keep/drop/retest",
        "max_live_slots",
        "max_contracts_per_entry",
        "RI excluded",
    ]
    for phrase in required_phrases:
        assert phrase in text

    assert json.loads(json.dumps(dashboard_payload, ensure_ascii=False))


def test_bundle_remains_read_only_and_no_live_orders(dashboard_bundle: dict) -> None:
    assert dashboard_bundle["dry_run_dir"].exists()
    assert dashboard_bundle["state_dir"].exists()
    assert dashboard_bundle["analytics_open_trades"]
    assert all(isinstance(row, dict) for row in dashboard_bundle["analytics_open_trades"])
    assert dashboard_bundle["broker_positions"]
    assert isinstance(dashboard_bundle["portfolio"], dict)
    assert len(dashboard_bundle["portfolio"].get("slots", {})) <= MAX_LIVE_SLOTS
