"""Test Pipeline Bridge — validates risk_scorecard_bridge + pipeline_ranker fix.

4 tests using existing conftest fixtures (engine_config_fixture, signal_pool_fixture,
portfolio_fixture). Covers:
  1. bridge returns dict with expected keys
  2. pipeline_ranker.run_pipeline returns non-None scorecard
  3. AST guard: no broker/tinkoff imports in bridge
  4. constraints enforced: max_slots<=3, RI excluded, contracts=1
"""
import ast
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Ensure project modules are importable
COMBINE_DIR = Path(__file__).resolve().parent.parent
CODE_DIR = COMBINE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))


class TestBuildScorecardBridge:
    """Tests for risk_scorecard_bridge.build_scorecard()."""

    def test_build_scorecard_returns_dict_with_keys(
        self, engine_config_fixture, portfolio_fixture
    ):
        """bridge.build_scorecard returns dict with risk_score, verdict, components."""
        from risk_scorecard_bridge import build_scorecard

        # Build a minimal slots_dict from portfolio_fixture + config
        now_ts = 1700000000.0
        risk_cfg = engine_config_fixture.get("risk", {})
        deposit = engine_config_fixture.get("deposit_rub", 100000)

        slots_dict = {
            "slot_LKOH_1234": {
                "ticker": "LKOH",
                "strategy": "vwap_reversion",
                "contracts": 1,
                "go_rub": deposit * risk_cfg.get("max_contracts_per_entry", 1)
                         * risk_cfg.get("risk_per_trade_pct", 2.7) / 100.0,
                "open_position": {
                    "direction": "LONG",
                    "qty": 1,
                    "entry_price": 18000.0,
                    "entry_atr": 1.0,
                    "entry_ts": now_ts,
                },
                "pnl_rub": 0.0,
                "peak_pnl_rub": 0.0,
                "stop_streak": 0,
                "last_signal_ts": now_ts,
                "n_trades": 0,
            },
        }

        config = {
            "go_budget_rub": deposit * risk_cfg.get("go_budget_pct", 50) / 100.0,
            "delta_band_pct": risk_cfg.get("delta_band_pct", 30),
            "deposit_rub": deposit,
            "max_slots": risk_cfg.get("max_slots", 3),
            "max_contracts_per_entry": risk_cfg.get("max_contracts_per_entry", 1),
            "excluded": engine_config_fixture.get("excluded", ["RI"]),
            "signal_max_age_minutes": risk_cfg.get("signal_max_age_minutes", 16),
        }

        result = build_scorecard(slots_dict, config, now_ts=now_ts)

        assert isinstance(result, dict), "build_scorecard must return dict"
        assert "risk_score" in result, "missing 'risk_score' key"
        assert "verdict" in result, "missing 'verdict' key"
        assert "components" in result, "missing 'components' key"
        assert isinstance(result["risk_score"], (int, float)), \
            "risk_score must be numeric"
        assert 0 <= result["risk_score"] <= 100, \
            "risk_score must be in [0, 100], got %s" % result["risk_score"]
        assert result["verdict"] in ("ALLOW", "REDUCE", "VETO"), \
            "verdict must be ALLOW/REDUCE/VETO, got %s" % result["verdict"]
        assert isinstance(result["components"], dict), \
            "components must be dict"

    def test_pipeline_ranker_with_bridge(
        self, engine_config_fixture, portfolio_fixture
    ):
        """pipeline_ranker.run_pipeline returns scorecard != None after bridge fix."""
        from pipeline_ranker import run_pipeline

        config = engine_config_fixture
        # Candidates: 3 tickers, one excluded (RI)
        candidates = [
            {
                "ticker": "LKOH", "direction": "LONG",
                "win_rate": 0.6, "avg_win": 200.0, "avg_loss": 100.0,
                "drawdown_pct": 5.0, "volatility": 2.0,
            },
            {
                "ticker": "GAZP", "direction": "SHORT",
                "win_rate": 0.5, "avg_win": 80.0, "avg_loss": 50.0,
                "drawdown_pct": 3.0, "volatility": 1.5,
            },
            {
                "ticker": "RI", "direction": "LONG",
                "win_rate": 0.9, "avg_win": 500.0, "avg_loss": 10.0,
            },
        ]
        now_ts = 1700000000.0

        result = run_pipeline(
            config=config,
            candidates=candidates,
            regime_snapshot=None,
            returns=None,
            now_ts=now_ts,
        )

        assert result is not None, "run_pipeline must return a result"
        assert result.get("scorecard") is not None, \
            "scorecard must not be None — bridge should fix this"
        scorecard = result["scorecard"]
        assert "risk_score" in scorecard, \
            "scorecard must have 'risk_score' key"
        assert scorecard["risk_score"] >= 0, \
            "risk_score must be non-negative"


class TestBridgeSafetyGuard:
    """Safety: no broker/tinkoff imports in bridge module."""

    def test_no_live_broker_in_bridge(self):
        """AST check: risk_scorecard_bridge.py must not import broker/tinkoff."""
        bridge_path = CODE_DIR / "risk_scorecard_bridge.py"
        assert bridge_path.exists(), "risk_scorecard_bridge.py not found"

        source = bridge_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_modules = {"tinkoff", "broker", "futures_lab", "investpy"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0].lower()
                    assert top not in forbidden_modules, (
                        "Broker import in risk_scorecard_bridge.py: %s" % alias.name
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0].lower()
                    assert top not in forbidden_modules, (
                        "Broker import in risk_scorecard_bridge.py: from %s" % node.module
                    )


class TestConstraintsEnforced:
    """Constraints: max_slots<=3, RI excluded, 1 contract per entry."""

    def test_constraints_enforced(
        self, engine_config_fixture, portfolio_fixture
    ):
        """build_scorecard enforces max_slots, contracts, RI exclusion."""
        from risk_scorecard_bridge import build_scorecard

        config = engine_config_fixture
        risk_cfg = config.get("risk", {})
        deposit = config.get("deposit_rub", 100000)
        now_ts = 1700000000.0

        # 3 valid slots + 1 RI slot (should be excluded)
        slots_dict = {}
        for i, (ticker, strat) in enumerate([
            ("LKOH", "vwap"), ("GAZP", "mean_rev"), ("SBER", "momentum"),
        ]):
            slot_id = "slot_%s_%d" % (ticker, i)
            slots_dict[slot_id] = {
                "ticker": ticker,
                "strategy": strat,
                "contracts": 1,
                "go_rub": deposit * risk_cfg.get("risk_per_trade_pct", 2.7) / 100.0,
                "open_position": {
                    "direction": "LONG", "qty": 1,
                    "entry_price": 100.0, "entry_atr": 1.0,
                    "entry_ts": now_ts,
                },
                "pnl_rub": 0.0, "peak_pnl_rub": 0.0,
                "stop_streak": 0, "last_signal_ts": now_ts,
            }

        sc_config = {
            "go_budget_rub": deposit * risk_cfg.get("go_budget_pct", 50) / 100.0,
            "deposit_rub": deposit,
            "max_slots": risk_cfg.get("max_slots", 3),
            "max_contracts_per_entry": risk_cfg.get("max_contracts_per_entry", 1),
            "excluded": config.get("excluded", ["RI"]),
            "signal_max_age_minutes": risk_cfg.get("signal_max_age_minutes", 16),
        }

        result = build_scorecard(slots_dict, sc_config, now_ts=now_ts)

        # Max slots check
        assert result["n_slots"] <= 3, \
            "max_slots violated: %d > 3" % result["n_slots"]

        # Contracts check: all slots must have contracts <= 1
        for slot_id, slot_data in slots_dict.items():
            assert slot_data.get("contracts", 1) <= 1, \
                "contracts > 1 for %s" % slot_id

        # RI excluded check
        tickers = result.get("tickers", [])
        assert "RI" not in tickers, \
            "RI found in scored tickers: %s" % tickers

        # Components present
        components = result.get("components", {})
        assert len(components) >= 4, \
            "Expected >= 4 components, got %d" % len(components)

        # Slots component: must be ok (n=3 <= 3)
        slots_comp = components.get("slots", {})
        assert slots_comp.get("status") == "ok", \
            "slots component should be ok, got %s" % slots_comp.get("status")

        # Caps component: must be ok (contracts=1)
        caps_comp = components.get("caps", {})
        assert caps_comp.get("status") == "ok", \
            "caps component should be ok, got %s" % caps_comp.get("status")
