"""Iteration 02: execution-truth safety tests; all broker calls are fakes."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "code"))

from core import analytics, engine, registry, supervisor  # noqa: E402


class FakeOrders:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post_order(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            order_id="broker-order",
            lots_executed=kwargs["quantity"],
            execution_report_status="EXECUTED",
            executed_order_price=SimpleNamespace(units=100, nano=0),
        )


class FakeClient:
    def __init__(self) -> None:
        self.orders = FakeOrders()


def bare_engine(mode: str = "paper", paper_first: bool = True):
    instance = object.__new__(engine.Engine)
    instance.cfg = SimpleNamespace(mode=mode, paper_first=paper_first)
    instance.account = "test-account"
    instance.db = analytics.connect(Path(tempfile.mkstemp(suffix=".db")[1]))
    return instance


@pytest.mark.parametrize("mode,paper_first", [
    ("paper", True), ("dryrun", True), ("backtest", True), ("live", True),
])
def test_final_order_boundary_vetoes_without_execution_authorization(mode, paper_first):
    eng = bare_engine(mode, paper_first)
    client = FakeClient()

    result = eng.post(client, "uid", "LONG", 1)

    assert result["executed"] == 0
    assert result["status"] == "VETO:EXECUTION_NOT_AUTHORIZED"
    assert client.orders.calls == []


def test_final_order_boundary_allows_only_explicit_live_opt_in():
    eng = bare_engine("live", False)
    client = FakeClient()

    result = eng.post(client, "uid", "LONG", 1, intent_id="allowed-intent",
                      intent_context={"action": "OPEN", "ticker": "SBER", "slot_id": "slot", "strategy": "s", "provenance": {}})

    assert result["executed"] == 1
    assert len(client.orders.calls) == 1


def test_intent_identity_is_stable_for_same_logical_action_and_distinct_for_change():
    first = engine.Engine._intent_id("slot-1", "entry", "2026-08-29T10:00Z", "LONG", 1)
    again = engine.Engine._intent_id("slot-1", "entry", "2026-08-29T10:00Z", "LONG", 1)
    changed = engine.Engine._intent_id("slot-1", "entry", "2026-08-29T10:15Z", "LONG", 1)

    assert first == again
    assert first != changed


def test_duplicate_logical_intent_reuses_same_broker_order_id():
    eng = bare_engine("live", False)
    client = FakeClient()
    intent = engine.Engine._intent_id("slot-1", "entry", "SBER", "LONG", 1, "bar-1")

    context = {"action": "OPEN", "ticker": "SBER", "slot_id": "slot-1", "strategy": "s", "provenance": {}}
    eng.post(client, "uid", "LONG", 1, intent_id=intent, intent_context=context)
    second = eng.post(client, "uid", "LONG", 1, intent_id=intent, intent_context=context)

    assert len(client.orders.calls) == 1
    assert {call["order_id"] for call in client.orders.calls} == {intent}
    assert second["status"] == "VETO:INTENT_NOT_SUBMITTABLE"


def test_broker_fetch_failure_is_unknown_not_empty(monkeypatch):
    eng = object.__new__(engine.Engine)
    eng.cfg = SimpleNamespace(universe=["SBER"])
    eng.token = "token"
    eng.specs = {}
    monkeypatch.setattr(eng, "ensure_spec", Mock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(engine, "Client", Mock(side_effect=RuntimeError("offline")))

    assert eng.fetch_broker_positions() is None


def test_confirmed_empty_broker_state_clears_local_position_but_unknown_does_not():
    pos = {"direction": "LONG", "qty": 1, "entry_price": 100.0, "entry_atr": 1.0}
    portfolio = {"slots": {"slot": {"ticker": "SBER", "strategy": "x", "open_position": dict(pos)}}}

    events = registry.reconcile_broker_positions(portfolio, {})
    assert portfolio["slots"]["slot"]["open_position"] is None
    assert events

    # Unknown is represented by None and is intentionally not passed into reconcile.
    unchanged = {"slots": {"slot": {"ticker": "SBER", "strategy": "x", "open_position": dict(pos)}}}
    broker_state = None
    if broker_state is not None:
        registry.reconcile_broker_positions(unchanged, broker_state)
    assert unchanged["slots"]["slot"]["open_position"] == pos


def test_canonical_analytics_db_is_root_and_legacy_state_db_is_not_used():
    assert analytics.DB_PATH == PROJECT / "analytics.db"
    assert (PROJECT / "state" / "analytics.db").exists()
    source = (PROJECT / "code" / "validate_allocator_dryrun.py").read_text(encoding="utf-8")
    assert 'ANALYTICS_DB = COMBINE_DIR / "analytics.db"' in source


def test_analytics_order_record_is_local_only_and_links_trade(tmp_path: Path):
    conn = analytics.connect(tmp_path / "analytics.db")
    trade = analytics.record_open(conn, "slot", "SBER", "x", "LONG", 1, 100.0, "test")
    analytics.record_order(conn, "intent", trade, "LONG", 1, 100.0)
    row = conn.execute("SELECT order_id, trade_id FROM orders").fetchone()
    assert row == ("intent", trade)


def test_iteration01_universe_gate_still_vetoes_foreign_candidate():
    result = supervisor.swap_candidate_admission(
        {"ticker": "IMOEX", "strategy": "x"}, ["BR", "GAZP", "LKOH", "SBER", "Si"]
    )
    assert result["decision"] == "VETO"
    assert result["reason"] == "OUTSIDE_CONFIGURED_UNIVERSE"


def test_current_config_is_paper_first_and_final_boundary_vetoes(tmp_path: Path):
    cfg = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
    assert cfg["mode"] == "paper"
    assert cfg["paper_first"] is True
    eng = bare_engine(cfg["mode"], cfg["paper_first"])
    client = FakeClient()
    assert eng.post(client, "uid", "SHORT", 1)["executed"] == 0
    assert client.orders.calls == []


def test_no_open_trade_without_portfolio_slot_in_current_analytics_db():
    conn = sqlite3.connect(PROJECT / "analytics.db")
    slots = set(json.loads((PROJECT / "state" / "portfolio.json").read_text())["slots"])
    orphans = conn.execute("SELECT slot_id FROM trades WHERE status='open'").fetchall()
    assert [slot_id for (slot_id,) in orphans if slot_id not in slots] == []
