"""Durable order-intent journal for broker-execution evidence.

The journal lives in the canonical analytics SQLite DB. It is not broker truth and
never authorizes a resubmission after an ambiguous broker outcome.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

NONTERMINAL = {"CREATED", "APPROVED", "SUBMITTING", "SUBMITTED", "UNKNOWN", "PARTIALLY_FILLED"}
TERMINAL = {"FILLED", "REJECTED", "CANCELLED", "FAILED_SAFE", "MANUAL_REVIEW_REQUIRED", "RECONCILED"}
_ALLOWED = {
    "CREATED": {"APPROVED", "FAILED_SAFE", "MANUAL_REVIEW_REQUIRED"},
    "APPROVED": {"SUBMITTING", "FAILED_SAFE", "MANUAL_REVIEW_REQUIRED"},
    "SUBMITTING": {"SUBMITTED", "FILLED", "REJECTED", "UNKNOWN", "MANUAL_REVIEW_REQUIRED"},
    "SUBMITTED": {"FILLED", "PARTIALLY_FILLED", "REJECTED", "UNKNOWN", "MANUAL_REVIEW_REQUIRED", "RECONCILED"},
    "UNKNOWN": {"FILLED", "REJECTED", "CANCELLED", "MANUAL_REVIEW_REQUIRED", "RECONCILED"},
    "PARTIALLY_FILLED": {"FILLED", "CANCELLED", "MANUAL_REVIEW_REQUIRED", "RECONCILED"},
    "FILLED": {"RECONCILED"},
    "REJECTED": set(), "CANCELLED": set(), "FAILED_SAFE": set(),
    "MANUAL_REVIEW_REQUIRED": set(), "RECONCILED": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_intents (
    intent_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    action TEXT NOT NULL,
    ticker TEXT NOT NULL,
    instrument_id TEXT,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    slot_id TEXT,
    strategy TEXT,
    provenance TEXT,
    mode TEXT NOT NULL,
    paper_first INTEGER NOT NULL,
    status TEXT NOT NULL,
    broker_client_order_id TEXT NOT NULL,
    broker_order_id TEXT,
    fill_quantity INTEGER NOT NULL DEFAULT 0,
    fill_price REAL,
    submission_attempt_count INTEGER NOT NULL DEFAULT 0,
    last_submission_at TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    reconciled_at TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_execution_intents_status ON execution_intents(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_execution_intents_broker ON execution_intents(broker_order_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _row(conn: sqlite3.Connection, intent_id: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM execution_intents WHERE intent_id=?", (intent_id,)).fetchone()


def create_intent(conn: sqlite3.Connection, *, intent_id: str, action: str, ticker: str,
                  instrument_id: str, side: str, quantity: int, slot_id: str | None,
                  strategy: str | None, provenance: dict[str, Any] | None,
                  mode: str, paper_first: bool) -> dict[str, Any]:
    """Persist write-ahead evidence; idempotent for an identical logical intent."""
    ensure_schema(conn)
    ts = now()
    payload = json.dumps(provenance or {}, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "INSERT OR IGNORE INTO execution_intents "
        "(intent_id,created_at,updated_at,action,ticker,instrument_id,side,quantity,slot_id,strategy,provenance,mode,paper_first,status,broker_client_order_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (intent_id, ts, ts, action, ticker, instrument_id, side, int(quantity), slot_id,
         strategy, payload, mode, int(bool(paper_first)), "CREATED", intent_id),
    )
    conn.commit()
    row = _row(conn, intent_id)
    assert row is not None
    immutable = (row["action"], row["ticker"], row["instrument_id"], row["side"], row["quantity"], row["slot_id"])
    requested = (action, ticker, instrument_id, side, int(quantity), slot_id)
    if immutable != requested:
        raise ValueError("intent_id collision with different logical order")
    return dict(row)


def transition(conn: sqlite3.Connection, intent_id: str, target: str, **updates: Any) -> dict[str, Any]:
    ensure_schema(conn)
    row = _row(conn, intent_id)
    if row is None:
        raise KeyError("unknown intent_id")
    old = row["status"]
    if target != old and target not in _ALLOWED.get(old, set()):
        raise ValueError("invalid intent transition %s -> %s" % (old, target))
    allowed_cols = {"broker_order_id", "fill_quantity", "fill_price", "submission_attempt_count",
                    "last_submission_at", "last_error_type", "last_error_message", "reconciled_at"}
    bad = set(updates) - allowed_cols
    if bad:
        raise ValueError("unsupported intent updates: %s" % sorted(bad))
    cols = ["status=?", "updated_at=?"]
    values: list[Any] = [target, now()]
    for key, value in updates.items():
        cols.append(key + "=?")
        values.append(value)
    values.append(intent_id)
    conn.execute("UPDATE execution_intents SET %s WHERE intent_id=?" % ", ".join(cols), values)
    conn.commit()
    updated = _row(conn, intent_id)
    assert updated is not None
    return dict(updated)


def claim_submission(conn: sqlite3.Connection, intent_id: str) -> bool:
    """Atomically claim one broker submission; UNKNOWN is deliberately not retryable."""
    ensure_schema(conn)
    ts = now()
    cur = conn.execute(
        "UPDATE execution_intents SET status='SUBMITTING', updated_at=?, "
        "submission_attempt_count=submission_attempt_count+1, last_submission_at=? "
        "WHERE intent_id=? AND status IN ('CREATED','APPROVED')",
        (ts, ts, intent_id),
    )
    conn.commit()
    return cur.rowcount == 1


def broker_result(conn: sqlite3.Connection, intent_id: str, *, broker_order_id: str | None,
                  executed: int, fill_price: float | None, status: str) -> dict[str, Any]:
    target = "FILLED" if int(executed or 0) > 0 else "REJECTED"
    return transition(conn, intent_id, target, broker_order_id=broker_order_id,
                      fill_quantity=int(executed or 0), fill_price=fill_price,
                      last_error_type=None, last_error_message=None)


def ambiguous(conn: sqlite3.Connection, intent_id: str, exc: BaseException) -> dict[str, Any]:
    return transition(conn, intent_id, "UNKNOWN", last_error_type=type(exc).__name__,
                      last_error_message=str(exc)[:500])


def unresolved(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM execution_intents WHERE status IN ('SUBMITTING','SUBMITTED','UNKNOWN','PARTIALLY_FILLED') ORDER BY updated_at"
    ).fetchall()]


def recover(conn: sqlite3.Connection, lookup: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None) -> list[dict[str, Any]]:
    """Resolve only read-only evidence; unresolved/unknown never resubmits.

    `lookup` is an injected broker read-only resolver. SDK capabilities exist, but
    broker duplicate-ID semantics are not asserted by this module.
    """
    results = []
    for item in unresolved(conn):
        evidence = lookup(item) if lookup else None
        if evidence and evidence.get("terminal") and int(evidence.get("executed", 0) or 0) > 0:
            result = transition(conn, item["intent_id"], "RECONCILED",
                                broker_order_id=evidence.get("broker_order_id") or item.get("broker_order_id"),
                                fill_quantity=int(evidence.get("executed", 0) or 0),
                                fill_price=evidence.get("fill_price"), reconciled_at=now())
            results.append({"intent_id": item["intent_id"], "outcome": "CONFIRMED", "record": result})
        elif evidence and evidence.get("terminal") and evidence.get("rejected"):
            result = transition(conn, item["intent_id"], "REJECTED",
                                broker_order_id=evidence.get("broker_order_id") or item.get("broker_order_id"))
            results.append({"intent_id": item["intent_id"], "outcome": "REJECTED", "record": result})
        else:
            result = transition(conn, item["intent_id"], "MANUAL_REVIEW_REQUIRED",
                                last_error_type="RECOVERY_AMBIGUOUS",
                                last_error_message="No terminal broker evidence; automatic resubmission forbidden")
            results.append({"intent_id": item["intent_id"], "outcome": "MANUAL_REVIEW_REQUIRED", "record": result})
    return results
