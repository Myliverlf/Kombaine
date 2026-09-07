import json
import sqlite3
from pathlib import Path

import pytest


def test_normalize_fill_price_scales_gazp_like_quote():
    from core.engine import Engine
    eng = Engine()
    assert eng.normalize_fill_price('GAZP', 8278.0, 82.78) == pytest.approx(82.78)
    assert eng.normalize_fill_price('LKOH', 42585.0, 42568.0) == pytest.approx(42585.0)


def test_futures_equity_is_deposit_plus_unrealized_pnl(monkeypatch):
    from core.engine import Engine
    eng = Engine()
    eng.cfg.deposit_rub = 21281
    class DummyClient: pass
    monkeypatch.setattr(eng, 'ensure_spec', lambda ticker: type('S', (), {'point_value': 1.0})())
    pnl = eng.futures_unrealized_pnl({'GAZP': {'qty': -1, 'avg_price': 82.78, 'last_price': 82.23}})
    assert pnl == pytest.approx(0.55)
    assert eng.cfg.deposit_rub + pnl == pytest.approx(21281.55)


def test_portfolio_slots_have_positive_go_and_one_ticker_one_open_slot():
    p = json.loads(Path('/root/prop-desk/strategy_combine/state/portfolio.json').read_text())
    open_tickers = []
    for sid, slot in p.get('slots', {}).items():
        assert float(slot.get('go_rub') or 0) > 0, sid
        if slot.get('open_position'):
            open_tickers.append(slot.get('ticker'))
            pos = slot['open_position']
            ep = float(pos.get('entry_price') or 0)
            if slot.get('ticker') == 'GAZP':
                assert ep < 1000
            sl = float(slot.get('sl_px') or 0)
            tp = float(slot.get('tp_px') or 0)
            if pos.get('direction') == 'LONG':
                assert sl < ep < tp
            if pos.get('direction') == 'SHORT':
                assert sl > ep > tp
    assert len(open_tickers) == len(set(open_tickers))


def test_no_orphan_open_trades():
    root = Path('/root/prop-desk/strategy_combine')
    p = json.loads((root / 'state/portfolio.json').read_text())
    slot_ids = set(p.get('slots', {}))
    con = sqlite3.connect(root / 'analytics.db')
    rows = list(con.execute("select id, slot_id from trades where status='open'"))
    assert [r for r in rows if r[1] not in slot_ids] == []
