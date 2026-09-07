"""Iteration 03 failure-first tests for durable execution intent journal."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import analytics, execution_journal as ej  # noqa: E402


def db(tmp_path: Path):
    return analytics.connect(tmp_path / "analytics.db")


def create(conn, intent_id="intent-1", action="OPEN"):
    return ej.create_intent(
        conn, intent_id=intent_id, action=action, ticker="SBER", instrument_id="uid",
        side="LONG", quantity=1, slot_id="slot", strategy="strategy",
        provenance={"bar": "1"}, mode="live", paper_first=False,
    )


def test_t1_stable_identity_survives_restart_and_duplicate_create(tmp_path: Path):
    path = tmp_path / "analytics.db"
    conn = analytics.connect(path)
    create(conn, "stable")
    conn.close()
    reopened = analytics.connect(path)
    row = create(reopened, "stable")
    assert row["intent_id"] == "stable"
    assert reopened.execute("SELECT COUNT(*) FROM execution_intents").fetchone()[0] == 1


def test_t2_write_ahead_and_atomic_claim_precedes_submit(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    assert ej.claim_submission(conn, "intent-1") is True
    assert ej.claim_submission(conn, "intent-1") is False
    row = conn.execute("SELECT status, submission_attempt_count FROM execution_intents").fetchone()
    assert tuple(row) == ("SUBMITTING", 1)


def test_t3_timeout_becomes_unknown_and_cannot_be_claimed_again(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    assert ej.claim_submission(conn, "intent-1")
    ej.ambiguous(conn, "intent-1", TimeoutError("network lost after request"))
    row = conn.execute("SELECT status, last_error_type FROM execution_intents").fetchone()
    assert tuple(row) == ("UNKNOWN", "TimeoutError")
    assert ej.claim_submission(conn, "intent-1") is False


def test_t4_recovery_after_broker_accept_does_not_resubmit(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    assert ej.claim_submission(conn, "intent-1")
    ej.ambiguous(conn, "intent-1", ConnectionError("response lost"))
    recovered = ej.recover(conn, lambda _: {"terminal": True, "executed": 1, "broker_order_id": "broker-1", "fill_price": 100.5})
    assert recovered[0]["outcome"] == "CONFIRMED"
    assert conn.execute("SELECT status FROM execution_intents").fetchone()[0] == "RECONCILED"
    assert ej.claim_submission(conn, "intent-1") is False


def test_t5_restart_unresolved_without_broker_evidence_requires_manual_review(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    assert ej.claim_submission(conn, "intent-1")
    recovered = ej.recover(conn)
    assert recovered[0]["outcome"] == "MANUAL_REVIEW_REQUIRED"
    assert conn.execute("SELECT status FROM execution_intents").fetchone()[0] == "MANUAL_REVIEW_REQUIRED"


def test_t6_two_workers_cannot_claim_same_intent(tmp_path: Path):
    path = tmp_path / "analytics.db"
    one = analytics.connect(path)
    create(one)
    two = analytics.connect(path)
    assert ej.claim_submission(one, "intent-1") is True
    assert ej.claim_submission(two, "intent-1") is False


def test_t7_t8_confirmed_or_rejected_result_is_recorded_without_position_claim(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    assert ej.claim_submission(conn, "intent-1")
    ej.broker_result(conn, "intent-1", broker_order_id="broker", executed=0, fill_price=None, status="REJECTED")
    assert tuple(conn.execute("SELECT status, fill_quantity FROM execution_intents").fetchone()) == ("REJECTED", 0)


def test_t9_ambiguous_close_never_becomes_terminal_close(tmp_path: Path):
    conn = db(tmp_path)
    create(conn, action="CLOSE")
    assert ej.claim_submission(conn, "intent-1")
    ej.ambiguous(conn, "intent-1", TimeoutError("close unknown"))
    assert conn.execute("SELECT status FROM execution_intents").fetchone()[0] == "UNKNOWN"


def test_t10_broker_result_links_client_intent_and_broker_order(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    assert ej.claim_submission(conn, "intent-1")
    ej.broker_result(conn, "intent-1", broker_order_id="broker-9", executed=1, fill_price=101.0, status="FILLED")
    row = conn.execute("SELECT intent_id, broker_client_order_id, broker_order_id, status FROM execution_intents").fetchone()
    assert tuple(row) == ("intent-1", "intent-1", "broker-9", "FILLED")


def test_invalid_transition_is_rejected(tmp_path: Path):
    conn = db(tmp_path)
    create(conn)
    with pytest.raises(ValueError):
        ej.transition(conn, "intent-1", "FILLED")


def test_engine_post_requires_write_ahead_context_for_explicit_live(tmp_path: Path):
    from core.engine import Engine
    eng = object.__new__(Engine)
    eng.cfg = SimpleNamespace(mode="live", paper_first=False)
    eng.account = "account"
    eng.db = analytics.connect(tmp_path / "analytics.db")
    fake_orders = SimpleNamespace(post_order=lambda **_: pytest.fail("broker must not be reached"))
    fake_client = SimpleNamespace(orders=fake_orders)
    result = eng.post(fake_client, "uid", "LONG", 1, intent_id="intent-1")
    assert result["status"] == "VETO:MISSING_INTENT_CONTEXT"


def test_engine_timeout_persists_unknown_before_return(tmp_path: Path):
    from core.engine import Engine
    eng = object.__new__(Engine)
    eng.cfg = SimpleNamespace(mode="live", paper_first=False)
    eng.account = "account"
    eng.db = analytics.connect(tmp_path / "analytics.db")
    fake_client = SimpleNamespace(orders=SimpleNamespace(post_order=lambda **_: (_ for _ in ()).throw(TimeoutError("timeout"))))
    result = eng.post(fake_client, "uid", "LONG", 1, intent_id="intent-1", intent_context={"action":"OPEN", "ticker":"SBER", "slot_id":"slot", "strategy":"s", "provenance":{}})
    assert result["status"] == "UNKNOWN:BROKER_EXCEPTION"
    assert eng.db.execute("SELECT status FROM execution_intents WHERE intent_id='intent-1'").fetchone()[0] == "UNKNOWN"


def test_engine_success_persists_before_and_after_broker_result(tmp_path: Path):
    from core.engine import Engine
    eng = object.__new__(Engine)
    eng.cfg = SimpleNamespace(mode="live", paper_first=False)
    eng.account = "account"
    eng.db = analytics.connect(tmp_path / "analytics.db")
    response = SimpleNamespace(order_id="broker-1", lots_executed=1, execution_report_status="FILLED", executed_order_price=SimpleNamespace(units=100, nano=0))
    client = SimpleNamespace(orders=SimpleNamespace(post_order=lambda **_: response))
    result = eng.post(client, "uid", "LONG", 1, intent_id="intent-1", intent_context={"action":"OPEN", "ticker":"SBER", "slot_id":"slot", "strategy":"s", "provenance":{}})
    assert result["executed"] == 1
    assert tuple(eng.db.execute("SELECT status, broker_order_id FROM execution_intents WHERE intent_id='intent-1'").fetchone()) == ("FILLED", "broker-1")


def test_iteration01_and_iteration02_regressions_are_importable():
    from core import supervisor
    assert supervisor.universe_admission("IMOEX", ["SBER"])["decision"] == "VETO"
    assert ej.NONTERMINAL
