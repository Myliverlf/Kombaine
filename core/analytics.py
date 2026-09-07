"""Analytics store: SQLite — трейды, слоты, факторы входов, фидбек генератору."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "analytics.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_open TEXT NOT NULL,
    ts_close TEXT,
    slot_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    contracts INTEGER NOT NULL DEFAULT 1,
    entry_price REAL,
    exit_price REAL,
    pnl_rub REAL,
    exit_reason TEXT,
    regime TEXT,
    hour INTEGER,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT,
    trade_id INTEGER,
    ts TEXT,
    side TEXT,
    qty INTEGER,
    price REAL
);
CREATE TABLE IF NOT EXISTS slot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    event TEXT NOT NULL,      -- promoted|ejected|retested|signal
    detail TEXT
);
CREATE TABLE IF NOT EXISTS generator_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL     -- json hints для ночного генератора
);
CREATE INDEX IF NOT EXISTS idx_trades_slot ON trades(slot_id, ts_open);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_orders_trade ON orders(trade_id, ts);
"""


def connect(db: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.executescript(SCHEMA)
    return conn


def record_open(conn, slot_id, ticker, strategy, direction, contracts, entry_price, regime, ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO trades (ts_open, slot_id, ticker, strategy, direction, contracts, entry_price, regime, hour, status)"
        " VALUES (?,?,?,?,?,?,?,?,?, 'open')",
        (ts, slot_id, ticker, strategy, direction, contracts, entry_price, regime,
         datetime.fromisoformat(ts.replace("Z", "+00:00")).hour if ts else None))
    conn.commit()
    return cur.lastrowid


def record_close(conn, trade_id, exit_price, pnl_rub, exit_reason):
    if trade_id is None:
        # Позиция adopt-ена из reconciliation без trade record — пропускаем обновление
        return
    conn.execute(
        "UPDATE trades SET ts_close=?, exit_price=?, pnl_rub=?, exit_reason=?, status='closed' WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), exit_price, pnl_rub, exit_reason, trade_id))
    conn.commit()


def record_order(conn, order_id, trade_id, side, qty, price):
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO orders (order_id, trade_id, ts, side, qty, price) VALUES (?,?,?,?,?,?)",
        (str(order_id) if order_id is not None else None, trade_id, ts, side, int(qty), float(price) if price is not None else None),
    )
    conn.commit()


def slot_pnls(conn, slot_id, last_n=20):
    rows = conn.execute(
        "SELECT pnl_rub FROM trades WHERE slot_id=? AND status='closed' ORDER BY ts_close DESC LIMIT ?",
        (slot_id, last_n)).fetchall()
    return [r[0] for r in reversed(rows)]


def strategy_stats(conn):
    rows = conn.execute("""
        SELECT strategy, ticker, COUNT(*) n, SUM(pnl_rub) pnl,
               AVG(CASE WHEN pnl_rub > 0 THEN 1.0 ELSE 0.0 END) win_rate
        FROM trades WHERE status='closed' GROUP BY strategy, ticker ORDER BY pnl DESC
    """).fetchall()
    return rows


def generator_hints(conn, days=14):
    """Агрегированные подсказки для ночного генератора: что даёт деньги."""
    rows = conn.execute("""
        SELECT direction, regime, SUM(pnl_rub) pnl, COUNT(*) n
        FROM trades WHERE status='closed' GROUP BY direction, regime
    """).fetchall()
    hour_rows = conn.execute("""
        SELECT hour, SUM(pnl_rub) pnl, COUNT(*) n FROM trades
        WHERE status='closed' GROUP BY hour ORDER BY pnl DESC LIMIT 5
    """).fetchall()
    return {"by_direction_regime": [dict(zip(["direction", "regime", "pnl", "n"], r)) for r in rows],
            "best_hours": [dict(zip(["hour", "pnl", "n"], r)) for r in hour_rows]}


if __name__ == "__main__":
    conn = connect()
    print("analytics.db ok:", DB_PATH)
    print(strategy_stats(conn))
