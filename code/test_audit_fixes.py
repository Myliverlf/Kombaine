#!/usr/bin/env python3
"""Тесты по FIX_SPEC аудита strategy_combine.

Запуск:
    cd /root/prop-desk/strategy_combine && python code/test_audit_fixes.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

COMBINE_DIR = Path("/root/prop-desk/strategy_combine")
sys.path.insert(0, str(COMBINE_DIR))

from core import analytics as an  # noqa: E402
from core import engine  # noqa: E402
from core import registry  # noqa: E402
from core import risk  # noqa: E402
from core import supervisor  # noqa: E402


class DummyLock:
    def __init__(self, raise_busy: bool = False):
        self.raise_busy = raise_busy
        self.closed = False

    def __enter__(self):
        if self.raise_busy:
            raise BlockingIOError()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def close(self):
        self.closed = True


def mk_cfg():
    risk_limits = SimpleNamespace(
        slot_eject_pf=0.9,
        slot_eject_window_trades=20,
        slot_eject_streak_stops=3,
        slot_eject_slot_drawdown_pct=25.0,
        slot_eject_silent_days=5,
        signal_pool_max=10,
        signal_rotation_days=3,
        signal_min_rank=100.0,
        signal_max_age_minutes=16,
        waitlist_max_retests=2,
        waitlist_max=20,
        promotion_margin_pct=5.0,
        max_slots=3,
        delta_band_pct=20.0,
        min_reserve_pct=10.0,
        max_contracts_per_entry=1,
    )
    return SimpleNamespace(
        deposit_rub=100_000.0,
        portfolio_stop_rub=25_000.0,
        go_budget_rub=50_000.0,
        risk=risk_limits,
        excluded=[],
        risk_per_trade_rub=1_000.0,
        atr_period=14,
        sl_atr_mult=2.0,
        tp_atr_mult=3.0,
        force_exit_hours=48,
    )


class FakePrice:
    def __init__(self, value: float):
        self.units = int(value)
        self.nano = int(round((value - int(value)) * 1e9))


class FakeOrder:
    def __init__(self, lots_executed=1, status="EXECUTED", price=100.0):
        self.lots_executed = lots_executed
        self.execution_report_status = status
        self.executed_order_price = FakePrice(price)


class FakeOrders:
    def __init__(self, price=100.0):
        self.price = price
        self.calls = []

    def post_order(self, **kwargs):
        self.calls.append(kwargs)
        return FakeOrder(price=self.price)


class FakeCandle:
    def __init__(self, t, o, h, l, c):
        self.time = t
        self.open = FakePrice(o)
        self.high = FakePrice(h)
        self.low = FakePrice(l)
        self.close = FakePrice(c)
        self.volume = 1


class FakeMarketData:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, **kwargs):
        return SimpleNamespace(candles=self._candles)


class FakeOperations:
    def __init__(self, money=100_000.0):
        self.money = money

    def get_portfolio(self, **kwargs):
        money_asset = SimpleNamespace(asset_type="money", quantity=FakePrice(self.money))
        return SimpleNamespace(assets=[money_asset], positions=[])


class FakeClient:
    def __init__(self, token=None, price=100.0, candles=None):
        self.token = token
        self.orders = FakeOrders(price=price)
        self.market_data = FakeMarketData(candles or [])
        self.operations = FakeOperations()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeEngine(engine.Engine):
    def __init__(self):
        self.cfg = mk_cfg()
        self.risk = risk.RiskManager(self.cfg)
        self.token = "token"
        self.account = "acc"
        self.db = an.connect(Path(tempfile.gettempdir()) / "audit_test.db")
        self.specs = {"TEST": SimpleNamespace(uid="UID1", active_margin=1000.0, point_value=1.0)}
        self.candles = {}
        self._current_df = None

    def ensure_spec(self, ticker):
        return self.specs[ticker]

    def fetch_broker_positions(self):
        return {}

    def post(self, client, uid, direction, qty, **kwargs):
        return {"order_id": "OID1", "executed": qty, "status": "EXECUTED", "fill_price": 123.45}

    def fetch_candles(self, ticker):
        return self._current_df.copy() if self._current_df is not None else super().fetch_candles(ticker)


def make_portfolio(slot=None, peak_equity=0.0, halted=False, halt_reason=None):
    return {
        "slots": {} if slot is None else {"slot1": slot},
        "peak_equity": peak_equity,
        "halted": halted,
        "halt_reason": halt_reason,
    }


def make_slot(open_position=None, n_trades=0, pnl_rub=0.0, peak_pnl_rub=0.0, stop_streak=0, last_signal_ts=None):
    return {
        "ticker": "TEST",
        "strategy": "strat",
        "params": {},
        "contracts": 1,
        "go_rub": 1000.0,
        "promoted_ts": time.time(),
        "open_position": open_position,
        "n_trades": n_trades,
        "pnl_rub": pnl_rub,
        "peak_pnl_rub": peak_pnl_rub,
        "stop_streak": stop_streak,
        "last_signal_ts": last_signal_ts if last_signal_ts is not None else time.time(),
    }


def test_flock_blocks_second_tick():
    with patch.object(supervisor.fcntl, "flock", side_effect=BlockingIOError()), \
         patch("sys.stderr", new=SimpleNamespace(write=lambda *args, **kwargs: None)):
        assert supervisor.main() is None


def test_peak_equity_and_halt():
    cfg = mk_cfg()
    rm = risk.RiskManager(cfg)
    assert rm.check_portfolio_stop(100_000.0, 70_000.0) is True
    assert rm.check_portfolio_stop(0.0, 50_000.0) is False

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "analytics.db"
        conn = an.connect(db_path)
        portfolio = make_portfolio(make_slot(), peak_equity=0.0)
        eq = 120_000.0
        portfolio["peak_equity"] = max(portfolio["peak_equity"], eq)
        assert portfolio["peak_equity"] == eq
        portfolio["peak_equity"] = max(portfolio["peak_equity"], 110_000.0)
        assert portfolio["peak_equity"] == eq
        assert rm.check_portfolio_stop(portfolio["peak_equity"], 89_999.0) is True
        assert rm.check_portfolio_stop(portfolio["peak_equity"], 100_000.0) is False
        portfolio["halted"] = True
        portfolio["halt_reason"] = "portfolio_stop"
        assert portfolio["halted"] and portfolio["halt_reason"] == "portfolio_stop"
        conn.close()


def test_eject_deferred_and_release():
    portfolio = {"slots": {"slot1": make_slot(open_position={"direction": "LONG", "qty": 1})}, "peak_equity": 0.0, "halted": False, "halt_reason": None}
    removed = registry.remove_slot(portfolio, "slot1", "R1")
    assert removed is None
    assert portfolio["slots"]["slot1"]["eject_pending"] == "R1"
    portfolio["slots"]["slot1"]["open_position"] = None
    removed = registry.remove_slot(portfolio, "slot1", "R1")
    assert removed is not None
    assert "slot1" not in portfolio["slots"]


def test_revive_rules():
    pool = {
        "strategies": {
            "A": {"ticker": "AAA", "strategy": "s1", "rank_score": 10.0, "status": "resolved_conflict", "added_ts": time.time(), "last_signal_ts": time.time()},
            "B": {"ticker": "BBB", "strategy": "s2", "rank_score": 20.0, "status": "expired", "added_ts": time.time(), "last_signal_ts": time.time()},
            "C": {"ticker": "BBB", "strategy": "s3", "rank_score": 15.0, "status": "active", "added_ts": time.time(), "last_signal_ts": time.time()},
        },
        "last_rotation_ts": 0.0,
    }
    removed = registry.rotate_signal_pool(pool, 10, now=time.time())
    assert removed == 0
    assert pool["strategies"]["A"]["status"] == "resolved_conflict"
    assert pool["strategies"]["B"]["status"] == "expired"


def test_load_save_corrupt_and_atomic():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "portfolio.json"
        bak = Path(td) / "portfolio.json.bak"
        p.write_text("{broken json")
        bak.write_text(json.dumps({"slots": {}, "peak_equity": 1.0, "halted": False, "halt_reason": None}))
        old_state = registry.PORTFOLIO
        try:
            registry.PORTFOLIO = p
            loaded = registry.load_portfolio()
            assert loaded["peak_equity"] == 1.0
            assert any(x.name.startswith("portfolio.json.corrupt-") for x in Path(td).iterdir())
            registry.save_portfolio({"slots": {}, "peak_equity": 2.0, "halted": False, "halt_reason": None})
            assert p.exists()
            assert not p.with_suffix(".tmp").exists()
        finally:
            registry.PORTFOLIO = old_state


def test_profit_factor_eject_logic():
    cfg = mk_cfg()
    slot = {"slot_id": "slotX", "n_trades": 20, "stop_streak": 0, "pnl_rub": 0.0, "peak_pnl_rub": 0.0, "last_signal_ts": time.time(), "trades": [100] * 10 + [-50] * 10}
    assert risk.eject_check(slot, cfg, time.time()) is None
    slot["trades"] = [100] * 5 + [-200] * 15
    assert risk.eject_check(slot, cfg, time.time()) is not None
    slot["trades"] = [100] * 20
    assert risk.eject_check(slot, cfg, time.time()) is None


def test_entry_price_uses_fill_price():
    eng = FakeEngine()
    now = datetime.now(timezone.utc)
    candles = []
    for i in range(100):
        t = now - timedelta(minutes=15 * (99 - i))
        candles.append(FakeCandle(t, 10 + i, 11 + i, 9 + i, 10.5 + i))
    eng._current_df = __import__("pandas").DataFrame([
        {"time": c.time, "open": float(c.open.units), "high": float(c.high.units), "low": float(c.low.units), "close": float(c.close.units), "volume": c.volume}
        for c in candles
    ])
    eng._current_df["atr"] = 1.0
    with patch.object(engine, "Client", FakeClient), \
         patch.object(engine, "build_signal", return_value=__import__("pandas").Series([0] * 98 + [1, 1])):
        portfolio = make_portfolio(make_slot())
        slot = portfolio["slots"]["slot1"]
        slot["params"] = {}
        pos = {}
        events = eng.process_slot(FakeClient(price=123.45, candles=candles), "slot1", slot, portfolio, pos)
        assert any("OPEN" in e for e in events)
        assert abs(slot["open_position"]["entry_price"] - 123.45) < 1e-9


def test_stale_candles_returns_reason():
    eng = FakeEngine()
    # Hard-stale threshold is 180 minutes; keep margin for test runtime.
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=181)
    df = __import__("pandas").DataFrame([
        {"time": stale_time - timedelta(minutes=15 * (99 - i)), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}
        for i in range(100)
    ])
    eng._current_df = df
    slot = make_slot()
    slot["params"] = {}
    with patch.object(engine, "build_signal", return_value=__import__("pandas").Series([0] * 100)):
        events = eng.process_slot(FakeClient(), "slot1", slot, make_portfolio(slot), {})
        assert events == ["TEST/strat: stale_candles"]


def test_order_logging_and_wal_connect():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "a.db"
        conn = an.connect(db)
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        assert journal == "wal"
        conn.execute("INSERT INTO trades (ts_open, slot_id, ticker, strategy, direction, contracts, entry_price, regime, hour, status) VALUES (?,?,?,?,?,?,?,?,?, 'open')",
                     (datetime.now(timezone.utc).isoformat(), "slot", "T", "s", "LONG", 1, 1.0, "live", 0))
        trade_id = conn.execute("SELECT id FROM trades").fetchone()[0]
        an.record_order(conn, "OID", trade_id, "LONG", 1, 1.23)
        row = conn.execute("SELECT order_id, trade_id, side, qty, price FROM orders").fetchone()
        assert row == ("OID", trade_id, "LONG", 1, 1.23)


def main():
    tests = [
        test_flock_blocks_second_tick,
        test_peak_equity_and_halt,
        test_eject_deferred_and_release,
        test_revive_rules,
        test_load_save_corrupt_and_atomic,
        test_profit_factor_eject_logic,
        test_entry_price_uses_fill_price,
        test_stale_candles_returns_reason,
        test_order_logging_and_wal_connect,
    ]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")


if __name__ == "__main__":
    main()
